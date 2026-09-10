from __future__ import annotations

import getpass
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from xenon.aggressiveness import LEVELS, describe_level, get_level
from xenon.config import (
    aggressiveness_from_config,
    config_password_matches,
    config_path,
    ensure_config,
    exclusion_policy_from_config,
    password_configured,
    save_config,
    set_config_password,
    targets_from_config,
)
from xenon.desktop import detect_desktop, install_trigger, parse_config_chord, render_trigger
from xenon.desktop.chord import parse_chord
from xenon.desktop.command import wrapper_path
from xenon.exclusions import OPTIONAL_DIR_CATALOG, PROTECTED_CATEGORIES
from xenon.fx import (
    ANIMATION_BY_KEY,
    ANIMATIONS,
    DISPLAY_MODES,
    EFFECT_ORDERS,
    EFFECTS,
    PRESET_BY_KEY,
    PRESETS,
    Playback,
    apply_preset,
    describe_fx,
    normalize_duration,
    playback_from_config,
    preview_panic_spectacle,
)
from xenon.panic import run_panic
from xenon.probe import probe_scope
from xenon.tui import Choice, STYLE, ask_text, clear_screen, pick, render_banner
from xenon.vault import (
    CIPHER_CHACHA,
    CIPHER_SPECS,
    generate_key_file,
    is_destroyed_vault,
    is_locked,
    is_passwordless_vault,
    is_read_protect_vault,
    load_optional_key_file,
    lock_directory,
    normalize_cipher,
    password_opens,
    rekey_directory,
    safe_file_size,
    unlock_directory,
    verify_directory,
)


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
    clear_screen()


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


def _trigger_status(session: SetupSession) -> str:
    raw = (session.config.get("trigger") or {}).get("chord")
    try:
        chord = parse_config_chord(raw)
    except ValueError:
        return STYLE.yellow(str(raw or "(invalid)"))
    desktop = (session.config.get("trigger") or {}).get("desktop") or detect_desktop()
    if desktop and desktop != "unknown":
        return f"{chord.display()}  {STYLE.dim(str(desktop))}"
    return chord.display()


def _kv(label: str, value: str) -> str:
    return f"{STYLE.dim(label.ljust(14))} {value}"


def _chip(label: str, value: str) -> str:
    return f"{STYLE.dim(label)} {value}"


def _status_lines(session: SetupSession) -> list[str]:
    targets = targets_from_config(session.config)
    if not targets:
        target_text = STYLE.yellow("none")
    elif len(targets) == 1:
        target_text = targets[0]
    else:
        target_text = f"{len(targets)} directories"

    cipher = session.config.get("encryption", {}).get("cipher") or "—"
    key_file = session.config.get("encryption", {}).get("key_file")
    password = STYLE.green("set") if _password_is_set(session) else STYLE.yellow("not set")

    locked_bits: list[str] = []
    for raw in targets:
        try:
            root = Path(raw).expanduser()
            if root.is_dir():
                locked_bits.append("yes" if is_locked(root) else "no")
        except OSError:
            locked_bits.append("?")
    if not locked_bits:
        locked = STYLE.dim("n/a")
    elif all(bit == "yes" for bit in locked_bits):
        locked = STYLE.magenta("locked")
    elif all(bit == "no" for bit in locked_bits):
        locked = STYLE.green("open")
    else:
        locked = STYLE.yellow("/".join(locked_bits))

    lines = [
        "  ".join(
            [
                _chip("Targets", str(target_text)),
                _chip("Password", password),
                _chip("Vaults", locked),
            ]
        ),
        "  ".join(
            [
                _chip("Crypto", f"{cipher} · {describe_level(aggressiveness_from_config(session.config))}"),
                _chip("Key", str(key_file) if key_file else STYLE.dim("none")),
            ]
        ),
        "  ".join(
            [
                _chip("Panic", _trigger_status(session)),
                _chip("FX", describe_fx(session.config)),
            ]
        ),
    ]
    if session.message:
        if session.message_ok is True:
            lines.append(STYLE.green(session.message))
        elif session.message_ok is False:
            lines.append(STYLE.red(session.message))
        else:
            lines.append(STYLE.yellow(session.message))
        session.message = ""
        session.message_ok = None
    return lines


def _status_block(session: SetupSession) -> None:
    for line in _status_lines(session):
        print(line)


def _home_choices(session: SetupSession) -> list[Choice]:
    targets = targets_from_config(session.config)
    n = len(targets)
    playback = describe_fx(session.config)
    return [
        Choice(
            "targets",
            "Targets",
            f"{n} director{'y' if n == 1 else 'ies'} to lock",
            group="Vault",
            shortcut="1",
        ),
        Choice(
            "password",
            "Password",
            "set" if _password_is_set(session) else "not set yet",
            group="Vault",
            shortcut="2",
        ),
        Choice(
            "verify",
            "Readiness",
            "probe whether files can be sealed",
            group="Vault",
            shortcut="3",
        ),
        Choice(
            "crypto",
            "Cipher & level",
            describe_level(aggressiveness_from_config(session.config)),
            group="Crypto",
            shortcut="4",
        ),
        Choice(
            "key",
            "Key file",
            "optional extra secret",
            group="Crypto",
            shortcut="5",
        ),
        Choice(
            "exclusions",
            "Exclusions",
            "skip caches, repos, and custom paths",
            group="Crypto",
            shortcut="6",
        ),
        Choice(
            "panic",
            "Panic",
            playback,
            group="Panic",
            shortcut="7",
        ),
        Choice("exit", "Exit", "leave setup", group="Session", shortcut="8"),
    ]


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
    if is_destroyed_vault(path):
        return STYLE.red("wiped")
    if is_read_protect_vault(path):
        return STYLE.yellow("sealed")
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


def _passworded_locked_roots(session: SetupSession) -> list[Path]:
    return [path for path in _locked_roots(session) if not is_passwordless_vault(path) and not is_destroyed_vault(path)]


def _password_is_set(session: SetupSession) -> bool:
    if password_configured(session.config):
        return True
    if session.password:
        return True
    return bool(_passworded_locked_roots(session))


def _remember_verified(session: SetupSession, password: str) -> None:
    session.password = password
    session.password_verified = bool(password)


def _clear_password(session: SetupSession) -> None:
    session.password = None
    session.password_verified = False


def _persist_password(session: SetupSession, password: str) -> None:
    if not password:
        return
    if (
        password_configured(session.config)
        and config_password_matches(session.config, password)
    ):
        return
    set_config_password(session.config, password)
    save_config(session.config, session.config_file)


def _read_new_password(*, session: SetupSession | None = None) -> str | None:
    password = getpass.getpass("New password: ")
    if not password:
        message = "Password cannot be empty."
        if session is not None:
            session.message = message
            session.message_ok = False
        else:
            print(STYLE.red(message))
        return None
    confirm_pw = getpass.getpass("Confirm password: ")
    if password != confirm_pw:
        message = "Passwords do not match."
        if session is not None:
            session.message = message
            session.message_ok = False
        else:
            print(STYLE.red(message))
        return None
    return password


def _rekey_locked_targets(
    session: SetupSession,
    old_password: str,
    new_password: str,
) -> None:
    cipher, key_file, policy = _session_crypto(session)
    for root in _locked_roots(session):
        if is_destroyed_vault(root):
            continue
        current = "" if is_passwordless_vault(root) else old_password
        rekey_directory(
            root,
            current,
            new_password,
            key_file=key_file,
            cipher_name=cipher,
            policy=policy,
        )


def _session_password_opens(
    session: SetupSession,
    password: str,
    roots: list[Path] | None = None,
) -> bool:
    if password_configured(session.config):
        if not config_password_matches(session.config, password):
            return False
    check = roots if roots is not None else _passworded_locked_roots(session)
    for root in check:
        if is_passwordless_vault(root):
            continue
        if not _password_opens_root(session, password, root):
            return False
    return True


def _require_existing_password(
    session: SetupSession,
    *,
    roots: list[Path] | None = None,
) -> str | None:
    passworded = [
        path
        for path in (roots if roots is not None else _locked_roots(session))
        if is_locked(path) and not is_passwordless_vault(path)
    ]
    password = getpass.getpass("Password: ")
    if not password or not _session_password_opens(session, password, passworded):
        _clear_password(session)
        print(STYLE.red("Wrong password."))
        return None
    _persist_password(session, password)
    _remember_verified(session, password)
    return password


def _ensure_lock_password(session: SetupSession) -> str | None:
    spec = get_level(aggressiveness_from_config(session.config))
    if spec.passwordless or (spec.destructive and not spec.needs_password):
        return ""
    if _password_is_set(session):
        return _require_existing_password(session)

    print()
    print(STYLE.yellow("This aggressiveness level requires a password."))
    password = _read_new_password()
    if password is None:
        return None
    try:
        _rekey_locked_targets(session, "", password)
    except Exception as exc:
        print(STYLE.red(f"Could not update locked targets: {exc}"))
        return None
    _persist_password(session, password)
    _remember_verified(session, password)
    return password


def _ensure_unlock_password(
    session: SetupSession,
    roots: list[Path],
) -> str | None:
    passworded = [
        path
        for path in roots
        if is_locked(path)
        and not is_passwordless_vault(path)
        and not is_destroyed_vault(path)
    ]
    if not passworded:
        return ""
    return _require_existing_password(session, roots=passworded)


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


def _confirm_destructive_lock(level: int) -> bool:
    spec = get_level(level)
    if not spec.destructive:
        return True
    print()
    if spec.level == 4:
        print(STYLE.yellow(STYLE.bold("This will delete files without overwriting.")))
        print(STYLE.dim("Data may still be recoverable from disk."))
        answer = input(STYLE.cyan("Type YES to delete files › ")).strip()
        return answer == "YES"
    print(STYLE.red(STYLE.bold("This will overwrite and permanently destroy files.")))
    print(STYLE.dim("This cannot be undone."))
    answer = input(STYLE.cyan("Type DESTROY to continue › ")).strip()
    return answer == "DESTROY"


def _run_lock_targets(session: SetupSession, selected: list[str]) -> None:
    level = aggressiveness_from_config(session.config)
    spec = get_level(level)
    if spec.destructive and not _confirm_destructive_lock(level):
        print(STYLE.yellow("Lock cancelled."))
        _pause()
        return

    password = _ensure_lock_password(session)
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
        print(STYLE.bold(f"{spec.title}  {root}"))
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
                aggressiveness=level,
            )
            locked += 1
        except Exception as exc:
            print(STYLE.red(f"Lock failed: {exc}"))
            failed += 1

    if locked and password:
        _persist_password(session, password)
        _remember_verified(session, password)

    print()
    print(
        f"{STYLE.green(f'{locked} applied')}  ·  "
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
    password = _ensure_unlock_password(session, locked_selected)
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
        password = _ensure_unlock_password(session, locked_selected)
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
            if is_destroyed_vault(root):
                print(STYLE.red("Destroyed — files cannot be restored."))
                continue
            if is_read_protect_vault(root):
                print(STYLE.dim("Read-protected — verifying sealed permissions."))
            else:
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
    spec = get_level(aggressiveness_from_config(session.config))
    print(f"  Action: {spec.title} — {spec.summary}")
    if spec.needs_password and not spec.destructive:
        print(
            STYLE.dim(
                "  Hotkey/setup panic does not prompt for a password; "
                "this level falls back to passwordless encryption."
            )
        )
    print(f"  Targets: {', '.join(selected)}")
    print(f"  Spectacle: {describe_fx(session.config)}")
    print(f"  Screen lock: {'yes' if lock_screen else 'no'}")
    print(f"  Kill session: {'yes' if kill_session else 'no'}")
    print()
    answer = input(STYLE.cyan("Type YES to continue › ")).strip()
    if answer != "YES":
        print(STYLE.yellow("Panic cancelled."))
        _pause()
        return

    try:
        run_panic(
            "",
            triggered=True,
            directories=[Path(raw) for raw in selected],
            config=session.config,
            config_file=session.config_file,
        )
    except Exception as exc:
        print(STYLE.red(f"Panic failed: {exc}"))
        _pause()
        return

    print()
    print(STYLE.green("Panic complete."))
    _pause()


def action_manage_targets(session: SetupSession) -> None:
    while True:
        targets = list(targets_from_config(session.config))
        header = _target_list_header(targets)
        items = [
            Choice("add", "Add directory", "new encryption target", group="Edit", shortcut="a"),
        ]
        if targets:
            items.append(
                Choice(
                    "one",
                    "Choose a target…",
                    "preview, lock, unlock, remove",
                    group="Edit",
                    shortcut="t",
                )
            )
            items.extend(
                [
                    Choice("lock", "Lock all", group="Every target", shortcut="l"),
                    Choice("unlock", "Unlock all", group="Every target", shortcut="u"),
                    Choice("check", "Check all", group="Every target", shortcut="c"),
                    Choice("panic", "Panic all", "uses the hotkey path", group="Every target", shortcut="!"),
                ]
            )
        items.append(Choice("back", "Back", group="Session", shortcut="b"))
        choice = pick(
            "Targets",
            items,
            subtitle="Directories Xenon encrypts in place.",
            header=header,
        )
        if choice in {None, "back"}:
            return
        if choice == "add":
            _add_target(session)
            continue
        if choice == "one":
            _act_on_one_target(session)
            continue
        if choice == "lock":
            _run_lock_targets(session, targets)
            continue
        if choice == "unlock":
            _run_unlock_targets(session, targets)
            continue
        if choice == "check":
            _run_check_targets(session, targets)
            continue
        if choice == "panic":
            _run_panic_targets(session, targets)
            continue


def _target_list_header(targets: list[str]) -> list[str]:
    if not targets:
        return [STYLE.yellow("No targets yet. Add a directory to get started.")]
    lines = []
    for index, raw in enumerate(targets, 1):
        path = Path(raw).expanduser()
        mark = _target_status_mark(path)
        lines.append(f"  {STYLE.cyan(f'{index:>2}')}  [{mark}]  {raw}")
    return lines


def _add_target(session: SetupSession) -> None:
    raw = ask_text("Directory to encrypt")
    if not raw:
        return
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        print(STYLE.red(f"Not a directory: {root}"))
        _pause()
        return
    targets = list(targets_from_config(session.config))
    existing = {str(Path(item).expanduser()) for item in targets}
    text = str(root)
    if str(root) in existing or text in existing:
        print(STYLE.yellow("Already listed."))
        _pause()
        return
    targets.append(text)
    session.config["targets"] = targets
    session.config.pop("source", None)
    save_config(session.config, session.config_file)
    count, _ = _preview_target(session, root)
    print()
    print(STYLE.green(f"Added target → {root}"))
    _pause()
    session.message = f"Added {root} ({count:,} files)."
    session.message_ok = True


def _act_on_one_target(session: SetupSession) -> None:
    targets = list(targets_from_config(session.config))
    if not targets:
        return
    picked = pick(
        "Choose a target",
        [
            Choice(
                str(index),
                raw,
                _target_status_mark(Path(raw).expanduser()),
            )
            for index, raw in enumerate(targets, 1)
        ],
        subtitle="Then pick what to do with it.",
    )
    if picked is None:
        return
    index = int(picked)
    selected = [targets[index - 1]]
    raw = selected[0]
    action = pick(
        raw,
        [
            Choice("preview", "Preview files", "list what would be sealed", shortcut="p"),
            Choice("lock", "Lock", shortcut="l"),
            Choice("unlock", "Unlock", shortcut="u"),
            Choice("check", "Check readiness", shortcut="c"),
            Choice("panic", "Panic this target", danger=True, shortcut="!"),
            Choice("remove", "Remove from list", danger=True, shortcut="d"),
            Choice("back", "Back", shortcut="b"),
        ],
    )
    if action in {None, "back"}:
        return
    if action == "remove":
        removed = targets.pop(index - 1)
        session.config["targets"] = targets
        session.config.pop("source", None)
        save_config(session.config, session.config_file)
        session.message = f"Removed {removed}."
        session.message_ok = True
        return
    if action == "preview":
        root = Path(raw).expanduser()
        if not root.is_dir():
            print(STYLE.red(f"Not a directory: {root}"))
            _pause()
            return
        _preview_target(session, root)
        print()
        _pause()
        return
    if action == "lock":
        _run_lock_targets(session, selected)
    elif action == "unlock":
        _run_unlock_targets(session, selected)
    elif action == "check":
        _run_check_targets(session, selected)
    elif action == "panic":
        _run_panic_targets(session, selected)


def action_set_password(session: SetupSession) -> None:
    _clear()
    _box_title("Set / change password")
    print(
        STYLE.dim(
            "This password locks and unlocks targets. Panic uses your hotkey "
            "instead of this password. It is stored as a verifier in config, "
            "never as plaintext."
        )
    )
    print()

    configured = password_configured(session.config)
    passworded = _passworded_locked_roots(session)
    has_existing = configured or bool(passworded)

    if has_existing:
        print(STYLE.dim("Enter the current password to change it."))
        print()
        old = getpass.getpass("Current password: ")
        if configured and not config_password_matches(session.config, old):
            _clear_password(session)
            session.message = "Wrong password. Password was not changed."
            session.message_ok = False
            return
        if passworded and not _session_password_opens(session, old, passworded):
            _clear_password(session)
            session.message = "Wrong password. Password was not changed."
            session.message_ok = False
            return
        if not old:
            _clear_password(session)
            session.message = "Wrong password. Password was not changed."
            session.message_ok = False
            return

        new = _read_new_password(session=session)
        if new is None:
            return
        try:
            _rekey_locked_targets(session, old, new)
        except Exception as exc:
            session.message = f"Could not update locked targets: {exc}"
            session.message_ok = False
            return
        set_config_password(session.config, new)
        save_config(session.config, session.config_file)
        _remember_verified(session, new)
        session.message = "Password updated."
        session.message_ok = True
        return

    print(STYLE.dim("No password is set yet. Choose one now."))
    print()
    new = _read_new_password(session=session)
    if new is None:
        return
    try:
        _rekey_locked_targets(session, "", new)
    except Exception as exc:
        session.message = f"Could not update locked targets: {exc}"
        session.message_ok = False
        return
    set_config_password(session.config, new)
    save_config(session.config, session.config_file)
    _remember_verified(session, new)
    session.message = "Password set."
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
    encryption = session.config.setdefault("encryption", {})
    current_level = aggressiveness_from_config(session.config)
    current_cipher = encryption.get("cipher")
    try:
        current_name = normalize_cipher(current_cipher)
    except ValueError:
        current_name = current_cipher

    level_key = pick(
        "Aggressiveness",
        [
            Choice(
                str(spec.level),
                spec.title,
                spec.summary,
                current=spec.level == current_level,
                danger=spec.destructive,
                shortcut=str(spec.level),
            )
            for spec in LEVELS
        ],
        subtitle="What lock and panic do to target files. Enter keeps the current level.",
        header=[
            _kv("Current", describe_level(current_level)),
            _kv("Cipher", str(current_cipher)),
        ],
    )
    if level_key is not None:
        spec = get_level(int(level_key))
        if spec.level == 5 and spec.level != current_level:
            if not password_configured(session.config):
                session.message = (
                    "Level 5 requires a password. Set one from Password first."
                )
                session.message_ok = False
                return
            print()
            print(STYLE.red(STYLE.bold("Highest-risk option")))
            print(
                STYLE.dim(
                    "Lock and panic will overwrite files multiple times, then delete them."
                )
            )
            password = getpass.getpass("Password: ")
            if not config_password_matches(session.config, password):
                session.message = "Wrong password. Aggressiveness was not changed."
                session.message_ok = False
                return
            confirm_text = input(STYLE.cyan("Type DESTROY to confirm › ")).strip()
            if confirm_text != "DESTROY":
                session.message = "Level 5 cancelled."
                return
        encryption["aggressiveness"] = spec.level
        current_level = spec.level

    cipher_key = pick(
        "Cipher",
        [
            Choice(
                spec.name,
                spec.name,
                spec.summary,
                current=spec.name == current_name,
                shortcut=str(index),
            )
            for index, spec in enumerate(CIPHER_SPECS, 1)
        ],
        subtitle="Used when encrypting (levels 2 and 3). Enter keeps the current cipher.",
    )
    if cipher_key is not None:
        encryption["cipher"] = normalize_cipher(cipher_key)

    save_config(session.config, session.config_file)
    session.message = (
        f"Aggressiveness {describe_level(current_level)}; "
        f"cipher {encryption.get('cipher')}."
    )
    session.message_ok = True


def action_encryption_key(session: SetupSession) -> None:
    current = session.config.get("encryption", {}).get("key_file")
    choice = pick(
        "Optional key file",
        [
            Choice("set", "Use an existing file", shortcut="1"),
            Choice("new", "Generate a new key file", shortcut="2"),
            Choice("clear", "Clear optional key file", shortcut="3"),
            Choice("back", "Back", shortcut="4"),
        ],
        subtitle="Mixed with your password via HKDF. Leave unset for password-only.",
        header=[_kv("Current", str(current or STYLE.dim("none")))],
    )
    encryption = session.config.setdefault("encryption", {})
    if choice in {None, "back"}:
        session.message = "Key file unchanged."
        return
    if choice == "set":
        raw = ask_text("Path to key file")
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
        return
    if choice == "new":
        default = Path.home() / ".config" / "xenon" / "xenon.key"
        raw = ask_text("Create key file at", default=str(default))
        path = Path(raw).expanduser()
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
        return
    encryption["key_file"] = None
    save_config(session.config, session.config_file)
    session.message = "Optional key file cleared."
    session.message_ok = True


def action_panic_hotkey(session: SetupSession) -> None:
    while True:
        raw = (session.config.get("trigger") or {}).get("chord")
        try:
            chord = parse_config_chord(raw)
            chord_text = chord.display()
        except ValueError:
            chord_text = str(raw or "(unset)")
        desktop = (session.config.get("trigger") or {}).get("desktop")
        detected = detect_desktop()
        playback = playback_from_config(session.config)
        preset_title = (
            PRESET_BY_KEY[playback.preset].title
            if playback.preset in PRESET_BY_KEY
            else "Custom"
        )
        header = [
            STYLE.dim("The chord runs panic immediately — no password."),
            _kv("Chord", chord_text),
            _kv("Desktop", f"{detected}  {STYLE.dim(str(desktop or 'auto'))}"),
            _kv("Preset", preset_title),
            _kv("Playback", describe_fx(session.config)),
        ]
        if playback.music:
            header.append(_kv("Music", playback.music))
        if playback.poweroff_after_track:
            header.append(_kv("After track", STYLE.red("power off")))
        choice = pick(
            "Panic",
            [
                Choice("chord", "Set hotkey", "example Super+Ctrl+Alt+Shift+X", group="Hotkey", shortcut="1"),
                Choice("install", "Install binding", "current desktop session", group="Hotkey", shortcut="2"),
                Choice("snippet", "Show bind snippet", group="Hotkey", shortcut="3"),
                Choice("preset", "Choose preset", preset_title, group="Spectacle", shortcut="4"),
                Choice("playback", "Customize playback", describe_fx(session.config), group="Spectacle", shortcut="5"),
                Choice("preview", "Preview", "no lock, no shutdown — Enter exits", group="Spectacle", shortcut="6"),
                Choice("back", "Back", group="Session", shortcut="7"),
            ],
            subtitle="Hotkey, presets, and how the spectacle plays.",
            header=header,
        )
        if choice in {None, "back"}:
            return
        if choice == "chord":
            typed = ask_text("New chord")
            if not typed:
                continue
            try:
                parsed = parse_chord(typed)
            except ValueError as exc:
                print(STYLE.red(str(exc)))
                _pause()
                continue
            session.config.setdefault("trigger", {})["chord"] = parsed.display()
            save_config(session.config, session.config_file)
            session.message = f"Panic hotkey set to {parsed.display()}."
            session.message_ok = True
            print(STYLE.green(session.message))
            bind = pick(
                "Install this binding now?",
                [
                    Choice("yes", "Yes, install on this desktop", shortcut="y"),
                    Choice("no", "Not now", shortcut="n"),
                ],
                allow_back=True,
            )
            if bind == "yes":
                _install_session_trigger(session)
            continue
        if choice == "install":
            _install_session_trigger(session)
            continue
        if choice == "snippet":
            print()
            print(
                render_trigger(
                    desktop or "auto",
                    chord=raw,
                    command=str(wrapper_path()),
                ),
                end="",
            )
            print()
            _pause()
            continue
        if choice == "preset":
            _choose_panic_preset(session)
            continue
        if choice == "playback":
            _customize_panic_playback(session)
            continue
        if choice == "preview":
            _run_fx_preview(session)
            continue


def _save_playback(session: SetupSession, playback: Playback) -> None:
    session.config["panic"] = playback.as_config()
    save_config(session.config, session.config_file)


def _mark_custom(playback: Playback) -> Playback:
    playback.preset = "custom"
    return playback


def _choose_panic_preset(session: SetupSession) -> None:
    current = playback_from_config(session.config)
    selected = pick(
        "Panic preset",
        [
            Choice(
                spec.key,
                spec.title,
                spec.summary,
                current=spec.key == current.preset,
                danger=spec.playback.poweroff_after_track,
                shortcut=str(index),
            )
            for index, spec in enumerate(PRESETS, 1)
        ],
        subtitle="Fills animation, effects, display, and shutdown. Your music file is kept.",
    )
    if selected is None:
        return
    playback = apply_preset(selected, music=current.music)
    _save_playback(session, playback)
    print(STYLE.green(f"Preset → {PRESET_BY_KEY[selected].title}"))
    if playback.poweroff_after_track:
        print(STYLE.red("This preset powers off after the track. Preview will not."))
    _pause()


def _choose_panic_animation(session: SetupSession) -> None:
    playback = playback_from_config(session.config)
    selected = pick(
        "Animation",
        [
            Choice(
                spec.key,
                spec.title,
                spec.summary,
                current=spec.key == playback.animation,
                shortcut=str(index),
            )
            for index, spec in enumerate(ANIMATIONS, 1)
        ],
    )
    if selected is None:
        return
    playback.animation = selected
    _save_playback(session, _mark_custom(playback))


def _toggle_panic_effects(session: SetupSession) -> None:
    while True:
        playback = playback_from_config(session.config)
        enabled_set = set(playback.effects)
        items = [
            Choice(
                spec.key,
                spec.title,
                ("on · " if spec.key in enabled_set else "off · ") + spec.summary,
                current=spec.key in enabled_set,
                shortcut=str(index),
            )
            for index, spec in enumerate(EFFECTS, 1)
        ]
        items.append(Choice("done", "Done", shortcut="b"))
        picked = pick(
            "Extra effects",
            items,
            subtitle="Select an effect to toggle. Music is configured separately.",
        )
        if picked in {None, "done"}:
            playback.effects = [spec.key for spec in EFFECTS if spec.key in enabled_set]
            _save_playback(session, _mark_custom(playback))
            return
        if picked in enabled_set:
            enabled_set.remove(picked)
        else:
            enabled_set.add(picked)
        playback.effects = [spec.key for spec in EFFECTS if spec.key in enabled_set]
        _save_playback(session, _mark_custom(playback))


def _customize_panic_playback(session: SetupSession) -> None:
    while True:
        playback = playback_from_config(session.config)
        choice = pick(
            "Playback",
            [
                Choice(
                    "animation",
                    "Animation",
                    ANIMATION_BY_KEY[playback.animation].title,
                    group="Look",
                ),
                Choice(
                    "effects",
                    "Extra effects",
                    ", ".join(playback.effects) if playback.effects else "none",
                    group="Look",
                ),
                Choice(
                    "display",
                    "Display",
                    playback.display,
                    group="Look",
                ),
                Choice(
                    "scripts",
                    "Script flash",
                    "on" if playback.script_flash else "off",
                    group="Look",
                ),
                Choice(
                    "order",
                    "Effect order",
                    playback.effect_order,
                    group="Look",
                ),
                Choice(
                    "duration",
                    "Duration",
                    f"{playback.duration:.1f}s",
                    group="Timing",
                ),
                Choice(
                    "music",
                    "Music file",
                    playback.music or "built-in siren if sound is on",
                    group="Timing",
                ),
                Choice(
                    "wait",
                    "Wait for track",
                    "yes" if playback.wait_for_music else "no",
                    group="Timing",
                ),
                Choice(
                    "poweroff",
                    "Power off after track",
                    "yes" if playback.poweroff_after_track else "no",
                    group="Timing",
                    danger=playback.poweroff_after_track,
                ),
                Choice("preview", "Preview", "no lock, no shutdown", group="Session"),
                Choice("back", "Back", group="Session"),
            ],
            subtitle="Select a row to change it. Enter or Esc goes back.",
        )
        if choice in {None, "back"}:
            return
        if choice == "animation":
            _choose_panic_animation(session)
            continue
        if choice == "effects":
            _toggle_panic_effects(session)
            continue
        if choice == "duration":
            raw = ask_text("Duration in seconds [0.8–30]")
            if not raw:
                continue
            try:
                playback.duration = normalize_duration(float(raw))
            except ValueError:
                print(STYLE.red("Not a number."))
                _pause()
                continue
            _save_playback(session, _mark_custom(playback))
            continue
        if choice == "display":
            selected = pick(
                "Display",
                [
                    Choice(key, title, summary, current=key == playback.display, shortcut=str(index))
                    for index, (key, title, summary) in enumerate(DISPLAY_MODES, 1)
                ],
            )
            if selected is None:
                continue
            playback.display = selected
            _save_playback(session, _mark_custom(playback))
            continue
        if choice == "music":
            raw = ask_text("Music file (empty clears)")
            if not raw:
                playback.music = ""
            else:
                path = Path(raw).expanduser()
                if not path.is_file():
                    print(STYLE.red(f"File not found: {path}"))
                    _pause()
                    continue
                playback.music = str(path)
            _save_playback(session, _mark_custom(playback))
            continue
        if choice == "wait":
            playback.wait_for_music = not playback.wait_for_music
            _save_playback(session, _mark_custom(playback))
            continue
        if choice == "poweroff":
            playback.poweroff_after_track = not playback.poweroff_after_track
            _save_playback(session, _mark_custom(playback))
            if playback.poweroff_after_track:
                print(STYLE.red("The machine will power off after panic + track. Preview will not."))
                _pause()
            continue
        if choice == "scripts":
            playback.script_flash = not playback.script_flash
            if playback.script_flash and playback.display == "overlay":
                playback.display = "tty"
            _save_playback(session, _mark_custom(playback))
            continue
        if choice == "order":
            selected = pick(
                "Effect order",
                [
                    Choice(key, title, summary, current=key == playback.effect_order, shortcut=str(index))
                    for index, (key, title, summary) in enumerate(EFFECT_ORDERS, 1)
                ],
            )
            if selected is None:
                continue
            playback.effect_order = selected
            _save_playback(session, _mark_custom(playback))
            continue
        if choice == "preview":
            _run_fx_preview(session)
            continue


def _run_fx_preview(session: SetupSession) -> None:
    print()
    print(
        STYLE.yellow(
            "Preview only — no lock, no wipe, no session kill, no shutdown."
        )
    )
    playback = playback_from_config(session.config)
    if playback.poweroff_after_track:
        print(STYLE.red("Power-off after track is configured and will be skipped."))
    print(STYLE.dim(f"Playing: {describe_fx(session.config)}  — Enter or Esc exits."))
    try:
        preview_panic_spectacle(session.config)
    except Exception as exc:
        print(STYLE.red(f"Preview failed: {exc}"))
    _pause()


def _install_session_trigger(session: SetupSession) -> None:
    raw = (session.config.get("trigger") or {}).get("chord")
    try:
        path = install_trigger("auto", chord=raw, ensure_config=False, config_file=session.config_file)
    except Exception as exc:
        session.message = f"Could not install panic hotkey: {exc}"
        session.message_ok = False
        print(STYLE.red(session.message))
        _pause()
        return
    detected = detect_desktop()
    session.config.setdefault("trigger", {})["desktop"] = detected
    save_config(session.config, session.config_file)
    session.message = (
        f"Panic hotkey installed for {detected} → {path}. "
        f"Wrapper: {wrapper_path()}"
    )
    session.message_ok = True
    print(STYLE.green(session.message))
    if detected == "unknown":
        print(
            STYLE.yellow(
                "Desktop was not detected. Bind the wrapper path in your "
                "compositor or desktop settings."
            )
        )
    _pause()


def action_manage_exclusions(session: SetupSession) -> None:
    policy = exclusion_policy_from_config(session.config)
    page = 0
    page_size = 8

    while True:
        names = sorted(OPTIONAL_DIR_CATALOG.keys())
        total_pages = max(1, (len(names) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        chunk = names[start : start + page_size]
        header = [STYLE.dim("Protected paths (OS, session, Xenon) stay skipped.")]
        for title, detail in PROTECTED_CATEGORIES:
            header.append(f"  {STYLE.green('✓')} {title}  {STYLE.dim(detail)}")
        items: list[Choice] = []
        for name in chunk:
            enabled = bool(policy.optional_dirs.get(name, True))
            items.append(
                Choice(
                    f"toggle:{name}",
                    name,
                    ("ON · " if enabled else "off · ") + OPTIONAL_DIR_CATALOG[name],
                    group=f"Skip these folders  ·  page {page + 1}/{total_pages}",
                    current=enabled,
                )
            )
        if total_pages > 1:
            if page > 0:
                items.append(Choice("prev", "Previous page", group="Browse", shortcut="p"))
            if page < total_pages - 1:
                items.append(Choice("next", "Next page", group="Browse", shortcut="n"))
        items.append(Choice("add", "Add custom path", group="Custom", shortcut="a"))
        for index, raw in enumerate(policy.custom_paths, 1):
            items.append(Choice(f"drop:{index - 1}", f"Remove {raw}", group="Custom"))
        items.append(Choice("refresh", "Refresh running-file scan", group="Session", shortcut="r"))
        items.append(Choice("done", "Save and back", group="Session", shortcut="b"))
        choice = pick(
            "Exclusions",
            items,
            subtitle="Select a folder name to toggle. These names are skipped under every target.",
            header=header,
        )
        if choice in {None, "done"}:
            session.config["exclusions"] = policy.to_config()
            save_config(session.config, session.config_file)
            session.message = "Exclusions saved."
            session.message_ok = True
            return
        if choice == "next":
            page = min(total_pages - 1, page + 1)
            continue
        if choice == "prev":
            page = max(0, page - 1)
            continue
        if choice == "refresh":
            count = len(policy.refresh_running_cache())
            session.message = f"Running-file protection refreshed ({count:,} paths)."
            session.message_ok = True
            continue
        if choice == "add":
            raw = ask_text("Path to exclude (absolute or target-relative)")
            if raw:
                if raw not in policy.custom_paths:
                    policy.custom_paths.append(raw)
                else:
                    print(STYLE.yellow("Already listed."))
                    _pause()
            continue
        if choice.startswith("toggle:"):
            name = choice.split(":", 1)[1]
            policy.optional_dirs[name] = not bool(policy.optional_dirs.get(name, True))
            continue
        if choice.startswith("drop:"):
            index = int(choice.split(":", 1)[1])
            try:
                policy.custom_paths.pop(index)
            except IndexError:
                continue
            continue


def run_setup(*, config_file: Path | None = None) -> int:
    target = config_file or config_path()
    config = ensure_config(target)
    session = SetupSession(config=config, config_file=target)

    actions = {
        "targets": action_manage_targets,
        "password": action_set_password,
        "verify": action_verify,
        "crypto": action_encryption_type,
        "key": action_encryption_key,
        "exclusions": action_manage_exclusions,
        "panic": action_panic_hotkey,
        "1": action_manage_targets,
        "2": action_set_password,
        "3": action_verify,
        "4": action_encryption_type,
        "5": action_encryption_key,
        "6": action_manage_exclusions,
        "7": action_panic_hotkey,
    }

    try:
        while True:
            choice = pick(
                "Setup",
                _home_choices(session),
                subtitle="Move with arrows or type a number. Enter selects.",
                header=_status_lines(session) + [STYLE.dim(str(session.config_file))],
            )
            if choice in {None, "exit", "8", "q"}:
                _clear()
                print(render_banner())
                print()
                print(STYLE.dim("Goodbye."))
                return 0
            action = actions.get(choice)
            if not action:
                session.message = "Invalid option."
                session.message_ok = False
                continue
            action(session)
    except KeyboardInterrupt:
        print("\n")
        print(STYLE.yellow("Interrupted. Exiting setup."))
        return 130
