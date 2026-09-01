from __future__ import annotations

import getpass
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from xenon.config import (
    config_path,
    ensure_config,
    exclusion_policy_from_config,
    save_config,
    targets_from_config,
)
from xenon.exclusions import OPTIONAL_DIR_CATALOG, PROTECTED_CATEGORIES
from xenon.lockdown import lockdown
from xenon.probe import probe_scope
from xenon.vault import (
    CIPHER_CHACHA,
    CIPHER_SPECS,
    SUPPORTED_CIPHERS,
    generate_key_file,
    is_locked,
    load_optional_key_file,
    lock_directory,
    normalize_cipher,
    password_opens,
    safe_file_size,
    unlock_directory,
    verify_directory,
)


BANNER_ART = r"""
/\ \ /\ \ /\  _`\ /\ \/\ \/\  __`\/\ \/\ \    
\ `\`\/'/'\ \ \L\_\ \ `\\ \ \ \/\ \ \ `\\ \   
 `\/ > <   \ \  _\L\ \ , ` \ \ \ \ \ \ , ` \  
    \/'/\`\ \ \ \L\ \ \ \`\ \ \ \_\ \ \ \`\ \ 
    /\_\\ \_\\ \____/\ \_\ \_\ \_____\ \_\ \_\
    \/_/ \/_/ \/___/  \/_/\/_/\/_____/\/_/\/_/
""".strip(
    "\n"
)

# Vertical green gradient (bright → dark), 256-color.
_BANNER_ROW = ("38;5;118", "38;5;82", "38;5;46", "38;5;40", "38;5;34", "38;5;22")


class Style:
    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self.wrap("1", text)

    def dim(self, text: str) -> str:
        return self.wrap("2", text)

    def cyan(self, text: str) -> str:
        return self.wrap("96", text)

    def green(self, text: str) -> str:
        return self.wrap("92", text)

    def yellow(self, text: str) -> str:
        return self.wrap("93", text)

    def red(self, text: str) -> str:
        return self.wrap("91", text)

    def magenta(self, text: str) -> str:
        return self.wrap("95", text)


STYLE = Style()


def render_banner() -> str:
    lines = BANNER_ART.splitlines()
    width = max(len(line) for line in lines)
    painted: list[str] = []

    for index, line in enumerate(lines):
        color = _BANNER_ROW[min(index, len(_BANNER_ROW) - 1)]
        padded = line.ljust(width)
        if STYLE.enabled:
            painted.append(f"\033[1;{color}m{padded}\033[0m")
        else:
            painted.append(padded)

    if not STYLE.enabled:
        return "\n".join(painted)

    rule = STYLE.dim("─" * (width + 2))
    top = f"{STYLE.dim('┌')}{rule}{STYLE.dim('┐')}"
    bottom = f"{STYLE.dim('└')}{rule}{STYLE.dim('┘')}"
    framed = [
        top,
        *[f"{STYLE.dim('│')} {row} {STYLE.dim('│')}" for row in painted],
        bottom,
    ]
    return "\n".join(framed)


@dataclass
class SetupSession:
    config: dict
    config_file: Path
    password: str | None = None
    password_verified: bool = False
    message: str = ""
    message_ok: bool | None = None
    preview_limit: int = 40


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause(label: str = "Press Enter to return to the menu...") -> None:
    input(STYLE.dim(label))


def _width() -> int:
    return max(60, min(shutil.get_terminal_size((80, 24)).columns, 100))


def _rule(char: str = "─") -> str:
    return char * _width()


def _box_title(title: str) -> None:
    print(render_banner())
    print()
    print(STYLE.bold(title))
    print(STYLE.dim(_rule()))


def _kv(label: str, value: str) -> str:
    return f"{STYLE.dim(label.ljust(14))} {value}"


def _status_block(session: SetupSession) -> None:
    targets = targets_from_config(session.config)
    if not targets:
        target_text = STYLE.yellow("(none)")
    elif len(targets) == 1:
        target_text = targets[0]
    else:
        target_text = f"{len(targets)} directories — {targets[0]} …"

    cipher = session.config.get("encryption", {}).get("cipher")
    key_file = session.config.get("encryption", {}).get("key_file") or STYLE.dim("(none)")
    if session.password_verified:
        password = STYLE.green("verified")
    elif session.password:
        password = STYLE.yellow("set for lock · not a vault login")
    else:
        password = STYLE.yellow("not set")

    locked = STYLE.dim("n/a")
    locked_bits: list[str] = []
    for raw in targets:
        try:
            root = Path(raw).expanduser()
            if root.is_dir():
                locked_bits.append("yes" if is_locked(root) else "no")
        except OSError:
            locked_bits.append("?")
    if locked_bits:
        if all(bit == "yes" for bit in locked_bits):
            locked = STYLE.magenta("yes")
        elif all(bit == "no" for bit in locked_bits):
            locked = STYLE.green("no")
        else:
            locked = STYLE.yellow("/".join(locked_bits))

    print(_kv("Targets", str(target_text)))
    print(_kv("Cipher", str(cipher)))
    print(_kv("Key file", str(key_file)))
    print(_kv("Password", password))
    print(_kv("Locked", locked))
    print(_kv("Config", str(session.config_file)))


def _render_menu(session: SetupSession) -> None:
    _clear()
    _box_title("Setup  ·  configure targets, crypto, and readiness")
    _status_block(session)
    print(STYLE.dim(_rule()))

    if session.message:
        if session.message_ok is True:
            print(STYLE.green(session.message))
        elif session.message_ok is False:
            print(STYLE.red(session.message))
        else:
            print(STYLE.yellow(session.message))
        print(STYLE.dim(_rule()))
        session.message = ""
        session.message_ok = None

    print()
    pwd_item = (
        "Enter master password"
        if _locked_roots(session)
        else "Set lock password"
    )
    items = [
        ("1", "Manage targets and preview files"),
        ("2", pwd_item),
        ("3", "Verify encryption readiness"),
        ("4", "Define encryption type"),
        ("5", "Define encryption key (optional)"),
        ("6", "Manage exclusions"),
        ("7", "Exit"),
    ]
    for number, label in items:
        print(f"  {STYLE.cyan(number)})  {label}")

    print()
    print(STYLE.dim("Ctrl+C to quit at any time."))
    print()


def _human_size(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num} B"


def _preview_target(session: SetupSession, root: Path) -> tuple[int, int]:
    from xenon.progress import Progress
    from xenon.walk import iter_files

    print()
    print(STYLE.dim(f"Scanning {root} with current exclusion policy…"))
    progress = Progress("Scanning")
    preview: list[tuple[Path, int]] = []
    total_bytes = 0
    count = 0
    limit = session.preview_limit
    policy = exclusion_policy_from_config(session.config)
    try:
        for path in iter_files(root, policy=policy):
            size = safe_file_size(path)
            if size is None:
                continue
            count += 1
            total_bytes += size
            if len(preview) < limit:
                preview.append((path, size))
            progress.update(1)
    finally:
        progress.close(suffix=f"{count:,} files")

    print()
    print(
        f"{STYLE.bold(f'{count:,}')} files  ·  "
        f"{STYLE.bold(_human_size(total_bytes))} total"
    )
    print(STYLE.dim(_rule("·")))

    if count == 0:
        print(STYLE.yellow("(no files found after exclusions)"))
    else:
        for index, (path, size) in enumerate(preview, 1):
            try:
                relative = path.relative_to(root).as_posix()
            except (ValueError, OSError):
                relative = str(path)
            print(
                f"  {STYLE.dim(f'{index:>3}.')} {relative}  "
                f"{STYLE.dim(f'({_human_size(size)})')}"
            )
        remaining = count - len(preview)
        if remaining > 0:
            print(STYLE.dim(f"  … and {remaining:,} more"))
    return count, total_bytes


_INDEX_REQUIRED = frozenset({"d", "rm", "remove", "p", "preview"})
_INDEX_OPTIONAL = frozenset({
    "l",
    "lock",
    "u",
    "unlock",
    "c",
    "check",
    "!",
    "panic",
})


def _target_status_mark(path: Path) -> str:
    if not path.exists() or not path.is_dir():
        return STYLE.red("missing")
    if is_locked(path):
        return STYLE.magenta("locked")
    return STYLE.green("dir")


def _split_target_command(choice: str) -> tuple[str, int | None] | None:
    parts = choice.split()
    if not parts:
        return None
    verb = parts[0]
    if verb not in _INDEX_REQUIRED and verb not in _INDEX_OPTIONAL:
        return None
    if len(parts) == 1:
        return verb, None
    if len(parts) != 2:
        return None
    try:
        return verb, int(parts[1])
    except ValueError:
        return None


def _select_targets(targets: list[str], index: int | None) -> list[str]:
    if not targets:
        raise ValueError("No targets configured.")
    if index is None:
        return list(targets)
    if index < 1 or index > len(targets):
        raise ValueError("Invalid target number.")
    return [targets[index - 1]]


def _locked_roots(session: SetupSession) -> list[Path]:
    found: list[Path] = []
    for raw in targets_from_config(session.config):
        path = Path(raw).expanduser()
        try:
            if path.is_dir() and is_locked(path):
                found.append(path)
        except OSError:
            continue
    return found


def _password_opens_root(
    session: SetupSession,
    password: str,
    root: Path,
) -> bool:
    _, key_file, _policy = _session_crypto(session)
    return password_opens(root, password, key_file=key_file)


def _authorize_password(
    session: SetupSession,
    password: str,
    roots: list[Path],
) -> bool:
    if not password:
        return False
    if not roots:
        return True
    return _password_opens_root(session, password, roots[0])


def _remember_verified(session: SetupSession, password: str) -> None:
    session.password = password
    session.password_verified = True


def _clear_password(session: SetupSession) -> None:
    session.password = None
    session.password_verified = False


def _ensure_password(
    session: SetupSession,
    *,
    confirm: bool = False,
    verify_roots: list[Path] | None = None,
) -> str | None:
    """Return a password for crypto ops. Unlock paths must open an existing vault."""
    roots = [path for path in (verify_roots or []) if is_locked(path)]

    if roots:
        if (
            session.password
            and session.password_verified
            and _authorize_password(session, session.password, roots)
        ):
            return session.password

        password = getpass.getpass("Master password: ")
        if not _authorize_password(session, password, roots):
            _clear_password(session)
            print(STYLE.red("Wrong password."))
            return None
        _remember_verified(session, password)
        return password

    if session.password:
        return session.password

    password = getpass.getpass("Master password: ")
    if not password:
        print(STYLE.red("Password cannot be empty."))
        return None
    if confirm:
        again = getpass.getpass("Confirm password: ")
        if password != again:
            print(STYLE.red("Passwords do not match."))
            return None
    session.password = password
    session.password_verified = False
    return password


def _session_crypto(session: SetupSession):
    encryption = session.config.get("encryption") or {}
    cipher = encryption.get("cipher") or CIPHER_CHACHA
    key_file = load_optional_key_file(encryption.get("key_file"))
    policy = exclusion_policy_from_config(session.config)
    return cipher, key_file, policy


def _print_probe_summary(report) -> None:
    for hint in report.root_hints:
        print(f"  {STYLE.dim('↳')} {hint}")
    if report.root_issues:
        print(STYLE.red(STYLE.bold("  Target issues")))
        for issue in report.root_issues:
            print(f"  {STYLE.red('✗')} {issue}")
    print(
        f"  {STYLE.green(f'{report.ok_count:,} ok')}  ·  "
        f"{STYLE.red(f'{report.fail_count:,} blocked')}  ·  "
        f"{STYLE.dim(f'{report.excluded_dirs:,} dirs excluded')}  ·  "
        f"{STYLE.dim(f'{report.excluded_files:,} files excluded')}  ·  "
        f"{report.scanned:,} scanned"
    )


def _run_lock_targets(session: SetupSession, selected: list[str]) -> None:
    existing = _locked_roots(session)
    password = _ensure_password(
        session,
        confirm=True,
        verify_roots=existing,
    )
    if password is None:
        _pause()
        return

    cipher, key_file, policy = _session_crypto(session)
    locked = 0
    skipped = 0
    failed = 0
    for raw in selected:
        root = Path(raw).expanduser()
        print()
        print(STYLE.bold(f"Lock  {root}"))
        if not root.is_dir():
            print(STYLE.red("Not a directory."))
            failed += 1
            continue
        if is_locked(root):
            print(STYLE.yellow("Already locked — skipped."))
            skipped += 1
            continue
        try:
            lock_directory(
                root,
                password,
                cipher_name=cipher,
                key_file=key_file,
                policy=policy,
            )
            locked += 1
        except Exception as exc:
            print(STYLE.red(f"Lock failed: {exc}"))
            failed += 1

    if locked:
        _remember_verified(session, password)

    print()
    print(
        f"{STYLE.green(f'{locked} locked')}  ·  "
        f"{STYLE.yellow(f'{skipped} skipped')}  ·  "
        f"{STYLE.red(f'{failed} failed')}"
    )
    _pause()


def _run_unlock_targets(session: SetupSession, selected: list[str]) -> None:
    locked_selected = [
        Path(raw).expanduser()
        for raw in selected
        if is_locked(Path(raw).expanduser())
    ]
    password = _ensure_password(session, verify_roots=locked_selected)
    if password is None:
        _pause()
        return

    _, key_file, _policy = _session_crypto(session)
    unlocked = 0
    skipped = 0
    failed = 0
    for raw in selected:
        root = Path(raw).expanduser()
        print()
        print(STYLE.bold(f"Unlock  {root}"))
        if not root.is_dir():
            print(STYLE.red("Not a directory."))
            failed += 1
            continue
        if not is_locked(root):
            print(STYLE.yellow("Not locked — skipped."))
            skipped += 1
            continue
        try:
            unlock_directory(root, password, key_file=key_file)
            unlocked += 1
        except Exception as exc:
            print(STYLE.red(f"Unlock failed: {exc}"))
            failed += 1

    print()
    print(
        f"{STYLE.green(f'{unlocked} unlocked')}  ·  "
        f"{STYLE.yellow(f'{skipped} skipped')}  ·  "
        f"{STYLE.red(f'{failed} failed')}"
    )
    _pause()


def _run_check_targets(session: SetupSession, selected: list[str]) -> None:
    _, key_file, policy = _session_crypto(session)
    needs_password = any(
        is_locked(Path(raw).expanduser()) for raw in selected
    )
    password = None
    if needs_password:
        locked_selected = [
            Path(raw).expanduser()
            for raw in selected
            if is_locked(Path(raw).expanduser())
        ]
        password = _ensure_password(session, verify_roots=locked_selected)
        if password is None:
            _pause()
            return

    for raw in selected:
        root = Path(raw).expanduser()
        print()
        print(STYLE.bold(f"Check  {root}"))
        if not root.exists() or not root.is_dir():
            print(STYLE.red(f"Not a usable directory: {root}"))
            continue
        if is_locked(root):
            print(STYLE.dim("Already locked — verifying ciphertext integrity."))
            try:
                verify_directory(root, password or "", key_file=key_file)
            except Exception as exc:
                print(STYLE.red(f"Verify failed: {exc}"))
            continue
        report = probe_scope(root, policy=policy)
        print()
        _print_probe_summary(report)
        if report.passed:
            print(STYLE.green("Ready — Xenon can encrypt this target."))
        else:
            print(STYLE.red("Not ready — use menu 3 to remediate."))

    print()
    _pause()


def _run_panic_targets(session: SetupSession, selected: list[str]) -> None:
    lockdown_cfg = session.config.get("lockdown") or {}
    enabled = bool(lockdown_cfg.get("enabled", True))
    lock_screen = enabled and bool(lockdown_cfg.get("lock_screen", True))
    kill_session = enabled and bool(lockdown_cfg.get("kill_session", True))

    print()
    print(STYLE.red(STYLE.bold("Panic")))
    print(f"  Encrypt: {', '.join(selected)}")
    print(f"  Screen lock: {'yes' if lock_screen else 'no'}")
    print(f"  Kill session: {'yes' if kill_session else 'no'}")
    print()
    answer = input(STYLE.cyan("Type YES to continue › ")).strip()
    if answer != "YES":
        print(STYLE.yellow("Panic cancelled."))
        _pause()
        return

    existing = _locked_roots(session)
    password = _ensure_password(
        session,
        confirm=True,
        verify_roots=existing,
    )
    if password is None:
        _pause()
        return

    backend = lockdown_cfg.get("backend", "auto")
    lock_command = lockdown_cfg.get("lock_command")
    kill_command = lockdown_cfg.get("kill_command")

    if lock_screen:
        lockdown(
            backend=backend,
            lock_screen=True,
            kill_session=False,
            lock_command=lock_command,
            kill_command=kill_command,
        )

    cipher, key_file, policy = _session_crypto(session)
    failed = False
    try:
        for raw in selected:
            root = Path(raw).expanduser()
            print()
            print(STYLE.bold(f"Panic lock  {root}"))
            if not root.is_dir():
                print(STYLE.red("Not a directory."))
                failed = True
                continue
            if is_locked(root):
                print(STYLE.yellow("Already locked — skipped."))
                continue
            lock_directory(
                root,
                password,
                cipher_name=cipher,
                key_file=key_file,
                policy=policy,
            )
    except Exception as exc:
        print(STYLE.red(f"Lock failed: {exc}"))
        failed = True
        if kill_session:
            lockdown(
                backend=backend,
                lock_screen=False,
                kill_session=True,
                lock_command=lock_command,
                kill_command=kill_command,
            )
        _pause()
        return

    _remember_verified(session, password)

    if kill_session:
        lockdown(
            backend=backend,
            lock_screen=False,
            kill_session=True,
            lock_command=lock_command,
            kill_command=kill_command,
        )

    print()
    if failed:
        print(STYLE.red("Panic finished with errors."))
    else:
        print(STYLE.green("Panic complete."))
    _pause()


def action_manage_targets(session: SetupSession) -> None:
    while True:
        _clear()
        _box_title("Manage targets")
        print(
            STYLE.dim(
                "Targets are directories Xenon will encrypt in place "
                "(lock / unlock / panic / check)."
            )
        )
        print(STYLE.dim("Omit N to apply lock / unlock / check / panic to every target."))
        print(STYLE.dim(_rule("·")))

        targets = list(targets_from_config(session.config))
        if not targets:
            print(STYLE.yellow("  (no targets configured)"))
        else:
            for index, raw in enumerate(targets, 1):
                path = Path(raw).expanduser()
                mark = _target_status_mark(path)
                print(f"  {STYLE.cyan(f'{index:>2}')}  [{mark}]  {raw}")

        print()
        print(STYLE.dim(_rule("·")))
        print(
            f"  {STYLE.cyan('a')} add   "
            f"{STYLE.cyan('d N')} remove   "
            f"{STYLE.cyan('p N')} preview   "
            f"{STYLE.cyan('s')} save   "
            f"{STYLE.cyan('b')} back"
        )
        print(
            f"  {STYLE.cyan('l')} [N] lock   "
            f"{STYLE.cyan('u')} [N] unlock   "
            f"{STYLE.cyan('c')} [N] check   "
            f"{STYLE.cyan('!')} [N] panic"
        )
        print()
        choice = input(STYLE.cyan("Targets › ")).strip().lower()

        if choice in {"b", "back", "q", "quit", ""}:
            return

        if choice in {"s", "save"}:
            session.config["targets"] = targets
            session.config.pop("source", None)
            save_config(session.config, session.config_file)
            print(STYLE.green("Saved."))
            _pause()
            session.message = f"{len(targets)} target(s) saved."
            session.message_ok = True
            return

        if choice in {"a", "add"}:
            raw = input(STYLE.cyan("Target directory › ")).strip()
            if not raw:
                continue
            root = Path(raw).expanduser().resolve()
            if not root.is_dir():
                print(STYLE.red(f"Not a directory: {root}"))
                _pause()
                continue
            text = str(root)
            existing = {str(Path(t).expanduser()) for t in targets}
            if str(root) in existing or text in existing:
                print(STYLE.yellow("Already listed."))
                _pause()
                continue
            targets.append(text)
            session.config["targets"] = targets
            session.config.pop("source", None)
            save_config(session.config, session.config_file)
            count, _ = _preview_target(session, root)
            print()
            print(STYLE.green(f"Added target → {root}"))
            _pause()
            session.message = f"Added target {root} ({count:,} files)."
            session.message_ok = True
            continue

        parsed = _split_target_command(choice)
        if parsed is None:
            print(STYLE.red("Unknown command."))
            _pause()
            continue

        verb, index = parsed
        if verb in _INDEX_REQUIRED and index is None:
            print(STYLE.red(f"Specify a target number, e.g. {verb} 1"))
            _pause()
            continue

        try:
            selected = _select_targets(targets, index)
        except ValueError as exc:
            print(STYLE.red(str(exc)))
            _pause()
            continue

        if verb in {"d", "rm", "remove"}:
            assert index is not None
            removed = targets.pop(index - 1)
            session.config["targets"] = targets
            session.config.pop("source", None)
            save_config(session.config, session.config_file)
            print(STYLE.green(f"Removed: {removed}"))
            _pause()
            session.message = f"Removed target {removed}."
            session.message_ok = True
            continue

        if verb in {"p", "preview"}:
            root = Path(selected[0]).expanduser()
            if not root.is_dir():
                print(STYLE.red(f"Not a directory: {root}"))
                _pause()
                continue
            _preview_target(session, root)
            print()
            _pause()
            continue

        if verb in {"l", "lock"}:
            _run_lock_targets(session, selected)
            continue

        if verb in {"u", "unlock"}:
            _run_unlock_targets(session, selected)
            continue

        if verb in {"c", "check"}:
            _run_check_targets(session, selected)
            continue

        if verb in {"!", "panic"}:
            _run_panic_targets(session, selected)
            continue

        print(STYLE.red("Unknown command."))
        _pause()


def action_set_password(session: SetupSession) -> None:
    _clear()
    locked = _locked_roots(session)

    if locked:
        _box_title("Enter master password")
        print(
            STYLE.dim(
                "Locked targets already have a vault password. "
                "Setup cannot change it. Enter the current password to authorize "
                "this session. To choose a new password, unlock first, then lock again."
            )
        )
        print()
        print(STYLE.dim(f"Locked: {locked[0]}"))
        print()
        password = getpass.getpass("Master password: ")
        if not _authorize_password(session, password, locked):
            _clear_password(session)
            session.message = "Wrong password. Vault password was not changed."
            session.message_ok = False
            return
        _remember_verified(session, password)
        session.message = "Password verified for this session."
        session.message_ok = True
        return

    _box_title("Set lock password")
    print(
        STYLE.dim(
            "Used only to lock unlocked targets in this session. "
            "Never written to disk. This does not change any existing vault."
        )
    )
    print()
    password = getpass.getpass("Master password: ")
    confirm_pw = getpass.getpass("Confirm password: ")
    if not password:
        session.message = "Password cannot be empty."
        session.message_ok = False
        return
    if password != confirm_pw:
        session.message = "Passwords do not match."
        session.message_ok = False
        return

    session.password = password
    session.password_verified = False
    session.message = "Lock password set for this session."
    session.message_ok = True


def action_verify(session: SetupSession) -> None:
    _clear()
    _box_title("Verify encryption readiness")
    print(
        STYLE.dim(
            "Live probe: exclusions and blocked paths stream as they are found.\n"
            "Status line shows ok / blocked / excluded / rate.\n"
            "If problems remain, you will be asked how to handle them."
        )
    )
    print(STYLE.dim(_rule("·")))

    if not targets_from_config(session.config):
        session.message = "Add at least one target first."
        session.message_ok = False
        return

    while True:
        policy = exclusion_policy_from_config(session.config)
        reports: list[tuple[str, object | None]] = []
        total_ok = 0
        total_fail = 0
        total_scanned = 0
        all_passed = True

        for raw in targets_from_config(session.config):
            root = Path(raw).expanduser()
            print()
            print(STYLE.bold(f"Target  {root}"))
            if not root.exists() or not root.is_dir():
                print(STYLE.red(f"  ✗ Not a usable directory: {root}"))
                reports.append((raw, None))
                all_passed = False
                continue

            report = probe_scope(root, policy=policy)
            reports.append((raw, report))
            print()
            for hint in report.root_hints:
                print(f"  {STYLE.dim('↳')} {hint}")
            if report.root_issues:
                print(STYLE.red(STYLE.bold("  Target issues")))
                for issue in report.root_issues:
                    print(f"  {STYLE.red('✗')} {issue}")

            print(
                f"  {STYLE.green(f'{report.ok_count:,} ok')}  ·  "
                f"{STYLE.red(f'{report.fail_count:,} blocked')}  ·  "
                f"{STYLE.dim(f'{report.excluded_dirs:,} dirs excluded')}  ·  "
                f"{STYLE.dim(f'{report.excluded_files:,} files excluded')}  ·  "
                f"{report.scanned:,} scanned"
            )
            total_ok += report.ok_count
            total_fail += report.fail_count
            total_scanned += report.scanned
            if not report.passed:
                all_passed = False

        print()
        print(STYLE.dim(_rule()))
        print(
            f"All targets  {STYLE.green(f'{total_ok:,} ok')}  ·  "
            f"{STYLE.red(f'{total_fail:,} blocked')}  ·  "
            f"{total_scanned:,} scanned"
        )

        if all_passed:
            print(STYLE.green("Ready — Xenon can reach the target files."))
            session.message = (
                f"Readiness OK — {total_ok:,} files across "
                f"{len(targets_from_config(session.config))} target(s)."
            )
            session.message_ok = True
            print()
            _pause()
            return

        print(STYLE.red("Not ready — choose how to proceed."))
        changed = _remediate_probe_issues(session, reports)
        if not changed:
            session.message = (
                f"Readiness failed — {total_fail:,} blocked / "
                f"{total_scanned:,} scanned."
            )
            session.message_ok = False
            print()
            _pause()
            return

        print()
        again = input(STYLE.cyan("Re-run readiness probe now? [Y/n] › ")).strip().lower()
        if again in {"n", "no"}:
            session.message = "Exclusions/targets updated — re-run option 3 to confirm."
            session.message_ok = True
            return
        print()
        print(STYLE.dim(_rule("·")))
        print(STYLE.dim("Re-probing with updated exclusions/targets…"))


def _custom_path_key(raw: str) -> str:
    return str(Path(raw).expanduser())


def _add_custom_exclusions(session: SetupSession, paths: list[Path | str]) -> int:
    exclusions = session.config.setdefault("exclusions", {})
    custom = list(exclusions.get("custom_paths") or [])
    existing = {_custom_path_key(item) for item in custom}
    added = 0
    for path in paths:
        text = str(path)
        key = _custom_path_key(text)
        if key in existing:
            continue
        custom.append(text)
        existing.add(key)
        added += 1
    exclusions["custom_paths"] = custom
    save_config(session.config, session.config_file)
    return added


def _remove_targets(session: SetupSession, remove: list[str]) -> int:
    remove_keys = {_custom_path_key(item) for item in remove}
    current = targets_from_config(session.config)
    kept = [item for item in current if _custom_path_key(item) not in remove_keys]
    removed = len(current) - len(kept)
    if removed:
        session.config["targets"] = kept
        session.config.pop("source", None)
        save_config(session.config, session.config_file)
    return removed


def _print_blocked_sample(blocked: list, *, limit: int = 12) -> None:
    for item in blocked[:limit]:
        issue = item.issues[0] if item.issues else "blocked"
        print(f"  {STYLE.red('✗')} {item.path}")
        print(f"      {STYLE.dim(issue)}")
    leftover = len(blocked) - limit
    if leftover > 0:
        print(STYLE.dim(f"  … and {leftover:,} more"))


def _target_is_hard_fail(raw: str, report) -> bool:
    root = Path(raw).expanduser()
    if report is None:
        return True
    if not root.exists() or not root.is_dir():
        return True
    if not os.access(root, os.R_OK | os.X_OK):
        return True
    markers = ("Permission", "Cannot enter", "not writable", "read-only", "does not exist")
    return any(
        any(marker in issue for marker in markers)
        for issue in report.root_issues
    )


# Remediation outcomes
_CHANGED = "changed"
_UNCHANGED = "unchanged"
_ABORT = "abort"


def _remediate_unusable_target(session: SetupSession, raw: str, report) -> str:
    root = Path(raw).expanduser()
    print()
    print(STYLE.bold(f"Unusable target  {root}"))
    if report is None:
        print(STYLE.dim("Missing or not a directory."))
    else:
        for issue in report.root_issues[:4]:
            print(f"  {STYLE.red('✗')} {issue}")
    print()
    print(f"  {STYLE.cyan('1')}) Remove this target from the list")
    print(f"  {STYLE.cyan('2')}) Exclude this path + remove it as a target")
    print(f"  {STYLE.cyan('3')}) Keep it — I'll fix permissions later")
    print(f"  {STYLE.cyan('4')}) Skip remaining prompts")
    choice = input(STYLE.cyan("Choose › ")).strip()

    if choice == "1":
        count = _remove_targets(session, [raw])
        print(STYLE.green(f"Removed {count} target."))
        return _CHANGED if count else _UNCHANGED

    if choice == "2":
        try:
            exclude_path = root.resolve()
        except OSError:
            exclude_path = root
        added = _add_custom_exclusions(session, [exclude_path])
        removed = _remove_targets(session, [raw])
        print(
            STYLE.green(
                f"Excluded path ({added} added) and removed as target ({removed})."
            )
        )
        return _CHANGED if (added or removed) else _UNCHANGED

    if choice == "4":
        return _ABORT

    print(STYLE.dim("Kept target unchanged."))
    return _UNCHANGED


def _remediate_blocked_files(session: SetupSession, raw: str, report) -> str:
    blocked = [item for item in report.results if not item.ok]
    if not blocked:
        return _UNCHANGED

    root = Path(raw).expanduser()
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root

    print()
    print(STYLE.bold(f"Blocked files under  {root}"))
    print(STYLE.dim(f"{len(blocked):,} path(s) cannot be encrypted in place."))
    _print_blocked_sample(blocked)
    print()
    print(f"  {STYLE.cyan('1')}) Exclude all blocked files")
    print(f"  {STYLE.cyan('2')}) Exclude parent directories of blocked files")
    print(f"  {STYLE.cyan('3')}) Decide one-by-one")
    print(f"  {STYLE.cyan('4')}) Leave blocked (do nothing)")
    print(f"  {STYLE.cyan('5')}) Skip remaining prompts")
    choice = input(STYLE.cyan("Choose › ")).strip()

    if choice == "5":
        return _ABORT
    if choice in {"", "4"}:
        print(STYLE.dim("Left blocked paths unchanged."))
        return _UNCHANGED

    if choice == "1":
        added = _add_custom_exclusions(session, [item.path for item in blocked])
        print(STYLE.green(f"Added {added} custom exclusion(s)."))
        return _CHANGED if added else _UNCHANGED

    if choice == "2":
        parents: list[Path] = []
        seen: set[str] = set()
        for item in blocked:
            try:
                parent = item.path.parent.resolve()
            except OSError:
                parent = item.path.parent
            key = str(parent)
            if key in seen or parent == root_resolved:
                continue
            seen.add(key)
            parents.append(parent)
        if not parents:
            print(
                STYLE.yellow(
                    "Blocked files sit directly in the target root — "
                    "excluding parents would skip the whole target. "
                    "Use option 1 (files) instead."
                )
            )
            return _UNCHANGED
        added = _add_custom_exclusions(session, parents)
        print(STYLE.green(f"Added {added} parent-directory exclusion(s)."))
        return _CHANGED if added else _UNCHANGED

    if choice == "3":
        changed = False
        for item in blocked:
            print()
            print(f"  {STYLE.red('✗')} {item.path}")
            for issue in item.issues[:2]:
                print(f"      {STYLE.dim(issue)}")
            print(
                f"  {STYLE.cyan('f')}) exclude file   "
                f"{STYLE.cyan('p')}) exclude parent   "
                f"{STYLE.cyan('s')}) skip   "
                f"{STYLE.cyan('q')}) quit list"
            )
            pick = input(STYLE.cyan("  › ")).strip().lower()
            if pick in {"q", "quit"}:
                break
            if pick in {"f", "file"}:
                if _add_custom_exclusions(session, [item.path]):
                    print(STYLE.green("    excluded file"))
                    changed = True
            elif pick in {"p", "parent"}:
                try:
                    parent = item.path.parent.resolve()
                except OSError:
                    parent = item.path.parent
                if parent == root_resolved:
                    print(STYLE.yellow("    parent is the target root — skipped"))
                elif _add_custom_exclusions(session, [parent]):
                    print(STYLE.green("    excluded parent"))
                    changed = True
            else:
                print(STYLE.dim("    skipped"))
        return _CHANGED if changed else _UNCHANGED

    print(STYLE.red("Unknown choice."))
    return _UNCHANGED


def _remediate_probe_issues(session: SetupSession, reports: list) -> bool:
    """Prompt for how to handle failed targets/files; apply exclusions."""
    any_change = False

    for raw, report in reports:
        if _target_is_hard_fail(raw, report):
            outcome = _remediate_unusable_target(session, raw, report)
            if outcome == _ABORT:
                return any_change
            if outcome == _CHANGED:
                any_change = True
            continue

        if report is not None and report.fail_count > 0:
            outcome = _remediate_blocked_files(session, raw, report)
            if outcome == _ABORT:
                return any_change
            if outcome == _CHANGED:
                any_change = True
            continue

        # Accessible but empty / otherwise not ready (e.g. no files after exclusions).
        if report is not None and not report.passed and report.root_issues:
            print()
            print(STYLE.bold(f"Target not ready  {Path(raw).expanduser()}"))
            for issue in report.root_issues[:4]:
                print(f"  {STYLE.red('✗')} {issue}")
            print()
            print(f"  {STYLE.cyan('1')}) Remove this target")
            print(f"  {STYLE.cyan('2')}) Keep it")
            print(f"  {STYLE.cyan('3')}) Skip remaining prompts")
            choice = input(STYLE.cyan("Choose › ")).strip()
            if choice == "1":
                if _remove_targets(session, [raw]):
                    print(STYLE.green("Removed target."))
                    any_change = True
            elif choice == "3":
                return any_change

    return any_change


def action_encryption_type(session: SetupSession) -> None:
    _clear()
    _box_title("Define encryption type")
    current = session.config.get("encryption", {}).get("cipher")
    try:
        current_name = normalize_cipher(current)
    except ValueError:
        current_name = current
    print(_kv("Current", str(current)))
    print()
    width = max(len(spec.name) for spec in CIPHER_SPECS)
    for index, spec in enumerate(CIPHER_SPECS, 1):
        marker = STYLE.green("  ← current") if spec.name == current_name else ""
        print(
            f"  {STYLE.cyan(str(index))}) {spec.name.ljust(width)}  "
            f"{STYLE.dim(spec.summary)}{marker}"
        )
    print()
    print(STYLE.dim("Enter a number, or type a cipher name."))
    choice = input(STYLE.cyan("Select cipher › ")).strip()
    if not choice:
        session.message = "Cipher unchanged."
        return

    selected: str | None = None
    try:
        index = int(choice)
        if 1 <= index <= len(SUPPORTED_CIPHERS):
            selected = SUPPORTED_CIPHERS[index - 1]
    except ValueError:
        pass

    if selected is None:
        try:
            selected = normalize_cipher(choice)
        except ValueError:
            session.message = "Invalid selection."
            session.message_ok = False
            return

    selected = normalize_cipher(selected)
    session.config.setdefault("encryption", {})["cipher"] = selected
    save_config(session.config, session.config_file)
    session.message = f"Cipher set to {selected}."
    session.message_ok = True


def action_encryption_key(session: SetupSession) -> None:
    _clear()
    _box_title("Define encryption key (optional)")
    print(STYLE.dim("Mixed with your password via HKDF. Leave unset for password-only."))
    print()
    current = session.config.get("encryption", {}).get("key_file")
    print(_kv("Current", str(current or STYLE.dim("(none)"))))
    print()
    print(f"  {STYLE.cyan('1')}) Set path to an existing key file")
    print(f"  {STYLE.cyan('2')}) Generate a new key file")
    print(f"  {STYLE.cyan('3')}) Clear optional key file")
    print(f"  {STYLE.cyan('4')}) Cancel")
    print()
    choice = input(STYLE.cyan("Select › ")).strip()

    encryption = session.config.setdefault("encryption", {})

    if choice == "1":
        raw = input(STYLE.cyan("Path to key file › ")).strip()
        if not raw:
            session.message = "Key file unchanged."
            return
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            session.message = f"File not found: {path}"
            session.message_ok = False
            return
        encryption["key_file"] = str(path)
        save_config(session.config, session.config_file)
        session.message = f"Key file set: {path}"
        session.message_ok = True

    elif choice == "2":
        default = Path.home() / ".config" / "xenon" / "xenon.key"
        raw = input(STYLE.cyan(f"Create key file at [{default}] › ")).strip()
        path = Path(raw).expanduser() if raw else default
        try:
            generate_key_file(path)
        except Exception as exc:
            session.message = f"Could not create key file: {exc}"
            session.message_ok = False
            return
        encryption["key_file"] = str(path.resolve())
        save_config(session.config, session.config_file)
        session.message = f"Generated key file: {path.resolve()}"
        session.message_ok = True

    elif choice == "3":
        encryption["key_file"] = None
        save_config(session.config, session.config_file)
        session.message = "Optional key file cleared."
        session.message_ok = True

    else:
        session.message = "Key file unchanged."


def action_manage_exclusions(session: SetupSession) -> None:
    policy = exclusion_policy_from_config(session.config)
    page = 0
    page_size = 18

    while True:
        _clear()
        _box_title("Manage exclusions")
        print(STYLE.bold("Protected (always skipped — cannot be toggled)"))
        print(STYLE.dim("These keep the OS, session, and Xenon itself runnable."))
        print()
        for title, detail in PROTECTED_CATEGORIES:
            print(f"  {STYLE.green('✓')} {STYLE.bold(title)}")
            print(f"      {STYLE.dim(detail)}")
        print()

        names = sorted(OPTIONAL_DIR_CATALOG.keys())
        total_pages = max(1, (len(names) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        chunk = names[start : start + page_size]

        print(
            STYLE.bold("Optional directory names")
            + STYLE.dim(f"  (page {page + 1}/{total_pages})")
        )
        print(STYLE.dim("Toggle folder names to skip anywhere under the targets."))
        print()

        for offset, name in enumerate(chunk):
            index = start + offset + 1
            enabled = bool(policy.optional_dirs.get(name, True))
            mark = STYLE.green("[x]") if enabled else STYLE.dim("[ ]")
            desc = OPTIONAL_DIR_CATALOG[name]
            print(f"  {mark} {STYLE.cyan(f'{index:>2}')}  {name:<28} {STYLE.dim(desc)}")

        print()
        print(STYLE.bold("Custom paths"))
        if policy.custom_paths:
            for index, raw in enumerate(policy.custom_paths, 1):
                print(f"  {STYLE.cyan(f'{index}')}  {raw}")
        else:
            print(STYLE.dim("  (none)"))

        print()
        print(STYLE.dim(_rule("·")))
        print(
            f"  {STYLE.cyan('t N')} toggle #N   "
            f"{STYLE.cyan('n')}/{STYLE.cyan('p')} next/prev page   "
            f"{STYLE.cyan('a')} add path"
        )
        print(
            f"  {STYLE.cyan('d N')} delete custom #N   "
            f"{STYLE.cyan('r')} refresh running scan   "
            f"{STYLE.cyan('s')} save   "
            f"{STYLE.cyan('b')} back"
        )
        print()
        choice = input(STYLE.cyan("Exclusions › ")).strip().lower()

        if choice in {"b", "back", ""}:
            session.config["exclusions"] = policy.to_config()
            save_config(session.config, session.config_file)
            session.message = "Exclusions saved."
            session.message_ok = True
            return

        if choice in {"s", "save"}:
            session.config["exclusions"] = policy.to_config()
            save_config(session.config, session.config_file)
            print(STYLE.green("Saved."))
            _pause()
            continue

        if choice in {"n", "next"}:
            page = min(total_pages - 1, page + 1)
            continue

        if choice in {"p", "prev"}:
            page = max(0, page - 1)
            continue

        if choice in {"r", "refresh"}:
            count = len(policy.refresh_running_cache())
            print(STYLE.green(f"Running-file protection refreshed ({count:,} paths)."))
            _pause()
            continue

        if choice == "a":
            raw = input(STYLE.cyan("Path to exclude (absolute or target-relative) › ")).strip()
            if raw:
                if raw not in policy.custom_paths:
                    policy.custom_paths.append(raw)
                    print(STYLE.green(f"Added: {raw}"))
                else:
                    print(STYLE.yellow("Already listed."))
            _pause()
            continue

        if choice.startswith("t "):
            try:
                index = int(choice.split(None, 1)[1])
                name = names[index - 1]
            except (ValueError, IndexError):
                print(STYLE.red("Invalid toggle number."))
                _pause()
                continue
            policy.optional_dirs[name] = not bool(policy.optional_dirs.get(name, True))
            state = "ON" if policy.optional_dirs[name] else "OFF"
            print(STYLE.green(f"{name} → {state}"))
            _pause()
            continue

        if choice.startswith("d "):
            try:
                index = int(choice.split(None, 1)[1])
                removed = policy.custom_paths.pop(index - 1)
            except (ValueError, IndexError):
                print(STYLE.red("Invalid custom path number."))
                _pause()
                continue
            print(STYLE.green(f"Removed: {removed}"))
            _pause()
            continue

        print(STYLE.red("Unknown command."))
        _pause()


def run_setup(*, config_file: Path | None = None) -> int:
    target = config_file or config_path()
    config = ensure_config(target)
    session = SetupSession(config=config, config_file=target)

    locked = _locked_roots(session)
    if locked:
        _clear()
        _box_title("Master password required")
        print(
            STYLE.dim(
                "One or more targets are locked. Enter the existing vault password "
                "to continue. Setup cannot replace that password."
            )
        )
        print()
        print(STYLE.dim(f"Locked: {locked[0]}"))
        print()
        try:
            password = getpass.getpass("Master password: ")
        except (EOFError, KeyboardInterrupt):
            print()
            print(STYLE.yellow("Cancelled."))
            return 130
        if not _authorize_password(session, password, locked):
            print(STYLE.red("Wrong password."))
            return 1
        _remember_verified(session, password)

    actions = {
        "1": action_manage_targets,
        "2": action_set_password,
        "3": action_verify,
        "4": action_encryption_type,
        "5": action_encryption_key,
        "6": action_manage_exclusions,
    }

    try:
        while True:
            _render_menu(session)
            choice = input(STYLE.cyan("Select [1-7] › ")).strip()
            if choice == "7":
                _clear()
                print(render_banner())
                print()
                print(STYLE.dim("Goodbye."))
                return 0
            action = actions.get(choice)
            if not action:
                session.message = "Invalid option. Choose 1-7."
                session.message_ok = False
                continue
            action(session)
    except KeyboardInterrupt:
        print("\n")
        print(STYLE.yellow("Interrupted. Exiting setup."))
        return 130
