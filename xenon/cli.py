from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from xenon.config import (
    aggressiveness_from_config,
    configured_targets,
    config_password_matches,
    exclusion_policy_from_config,
    load_config,
    password_configured,
    save_config,
    set_config_password,
    write_default_config,
)
from xenon.aggressiveness import get_level
from xenon.desktop import install_trigger, render_trigger
from xenon.panic import run_panic
from xenon.probe import probe_scope
from xenon.prompt import confirm, prompt_password
from xenon.setup_ui import run_setup
from xenon.vault import (
    CIPHER_CHACHA,
    is_locked,
    is_destroyed_vault,
    is_passwordless_vault,
    load_optional_key_file,
    lock_directory,
    unlock_directory,
    verify_directory,
)


def _encryption_from_config(config_file: Path | None = None):
    try:
        config = load_config(config_file)
    except FileNotFoundError:
        return CIPHER_CHACHA, None

    encryption = config.get("encryption") or {}
    cipher = encryption.get("cipher") or CIPHER_CHACHA
    key_file = load_optional_key_file(encryption.get("key_file"))
    return cipher, key_file


def _optional_config(config_file: Path | None = None) -> dict | None:
    try:
        return load_config(config_file)
    except FileNotFoundError:
        return None


def _cli_lock_password(config: dict | None, config_file: Path | None) -> str:
    if password_configured(config):
        password = getpass.getpass("Password: ")
        if not config_password_matches(config, password):
            raise ValueError("Wrong password.")
        return password

    print("This aggressiveness level requires a password.")
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not password:
        raise ValueError("Password cannot be empty.")
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    if config is not None:
        set_config_password(config, password)
        save_config(config, config_file)
    return password


def _cli_unlock_password(
    directories: list[Path],
    config: dict | None,
) -> str:
    passworded = [
        path.expanduser()
        for path in directories
        if is_locked(path.expanduser())
        and not is_passwordless_vault(path.expanduser())
        and not is_destroyed_vault(path.expanduser())
    ]
    if not passworded:
        return ""
    password = getpass.getpass("Password: ")
    if password_configured(config) and not config_password_matches(config, password):
        raise ValueError("Wrong password.")
    return password


def _resolve_directories(
    directory: Path | None,
    config_file: Path | None = None,
) -> list[Path]:
    if directory is not None:
        return [directory.expanduser()]

    try:
        config = load_config(config_file)
    except FileNotFoundError as exc:
        raise ValueError(
            "No directory given and no targets configured.\n"
            "Run: xenon setup   or   xenon lock /path/to/dir"
        ) from exc

    paths = configured_targets(config)
    if not paths:
        raise ValueError(
            "No directory given and no targets configured.\n"
            "Run: xenon setup   or   xenon lock /path/to/dir"
        )

    missing = [str(path) for path in paths if not path.is_dir()]
    if missing:
        raise ValueError(
            "Configured target(s) are not directories:\n  - " + "\n  - ".join(missing)
        )
    return paths


def _print_probe_report(report) -> None:
    for hint in report.root_hints:
        print(f"  ↳ {hint}")
    if report.root_hints:
        print()

    if report.root_issues:
        print("Target issues:")
        for issue in report.root_issues:
            print(f"  ✗ {issue}")
        print()

    print(
        f"{report.ok_count:,} ok · {report.fail_count:,} blocked · "
        f"{report.excluded_dirs:,} dirs excluded · "
        f"{report.excluded_files:,} files excluded · "
        f"{report.scanned:,} scanned"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xenon",
        description=(
            "Project Xenon — portable in-place file encryption "
            "with optional panic lockdown."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "setup",
        help="Interactive setup / configuration screen.",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Write default config under ~/.config/xenon/.",
    )
    init_parser.add_argument("--force", action="store_true")

    lock_parser = subparsers.add_parser(
        "lock",
        help="Encrypt directories in place (defaults to configured targets).",
    )
    lock_parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=None,
        help="Directory to lock (default: all config targets).",
    )
    lock_parser.add_argument("--config", type=Path)
    lock_parser.add_argument("--key-file", type=Path)

    unlock_parser = subparsers.add_parser(
        "unlock",
        help="Decrypt directories in place (defaults to configured targets).",
    )
    unlock_parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=None,
        help="Directory to unlock (default: all config targets).",
    )
    unlock_parser.add_argument("--config", type=Path)
    unlock_parser.add_argument("--key-file", type=Path)

    verify_parser = subparsers.add_parser(
        "verify",
        help="Cryptographically verify locked directories.",
    )
    verify_parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=None,
        help="Locked directory (default: all config targets).",
    )
    verify_parser.add_argument("--config", type=Path)
    verify_parser.add_argument("--key-file", type=Path)

    check_parser = subparsers.add_parser(
        "check",
        help="Probe whether target files can be read and rewritten.",
    )
    check_parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=None,
        help="Directory to probe (default: all config targets).",
    )
    check_parser.add_argument("--config", type=Path)

    panic_parser = subparsers.add_parser(
        "panic",
        help="Encrypt configured targets, then run optional lockdown.",
    )
    panic_parser.add_argument(
        "--gui",
        action="store_true",
        help="Prefer a GUI password prompt when available.",
    )
    panic_parser.add_argument("--yes", action="store_true")
    panic_parser.add_argument(
        "--target",
        "--source",
        dest="target",
        type=Path,
        help="Lock only this directory instead of all configured targets.",
    )
    panic_parser.add_argument("--no-lock-screen", action="store_true")
    panic_parser.add_argument("--no-kill-session", action="store_true")
    panic_parser.add_argument("--config", type=Path)

    trigger_parser = subparsers.add_parser(
        "install-trigger",
        help="Install an optional desktop hotkey hook.",
    )
    trigger_parser.add_argument(
        "--desktop",
        default="hyprland",
        help="Desktop environment hook to install (default: hyprland).",
    )
    trigger_parser.add_argument("--chord")
    trigger_parser.add_argument("--print-only", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        return run_setup()

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None or args.command == "setup":
        return run_setup()

    try:
        if args.command == "init":
            path = write_default_config(force=args.force)
            print(f"Wrote config: {path}")
            print("Run: xenon setup")

        elif args.command == "lock":
            directories = _resolve_directories(args.directory, args.config)
            cipher, key_file = _encryption_from_config(args.config)
            if args.key_file:
                key_file = args.key_file
            try:
                policy = exclusion_policy_from_config(load_config(args.config))
            except FileNotFoundError:
                policy = exclusion_policy_from_config(None)
            config = _optional_config(args.config)
            level = aggressiveness_from_config(config)
            spec = get_level(level)
            if spec.destructive:
                prompt = (
                    "Overwrite and permanently destroy files?"
                    if spec.level == 5
                    else "Delete files without overwriting?"
                )
                if not confirm(text=prompt):
                    print("Cancelled.")
                    return 0
            if spec.passwordless or (spec.destructive and not spec.needs_password):
                password = ""
            else:
                password = _cli_lock_password(config, args.config)
            for directory in directories:
                print(f"Locking {directory} …")
                lock_directory(
                    directory,
                    password,
                    cipher_name=cipher,
                    key_file=key_file,
                    policy=policy,
                    aggressiveness=level,
                )

        elif args.command == "unlock":
            directories = _resolve_directories(args.directory, args.config)
            _, key_file = _encryption_from_config(args.config)
            if args.key_file:
                key_file = args.key_file
            config = _optional_config(args.config)
            password = _cli_unlock_password(directories, config)
            for directory in directories:
                print(f"Unlocking {directory} …")
                unlock_directory(
                    directory,
                    password,
                    key_file=key_file,
                )

        elif args.command == "verify":
            directories = _resolve_directories(args.directory, args.config)
            _, key_file = _encryption_from_config(args.config)
            if args.key_file:
                key_file = args.key_file
            config = _optional_config(args.config)
            password = _cli_unlock_password(directories, config)
            for directory in directories:
                print(f"Verifying {directory} …")
                verify_directory(
                    directory,
                    password,
                    key_file=key_file,
                )

        elif args.command == "check":
            directories = _resolve_directories(args.directory, args.config)
            try:
                policy = exclusion_policy_from_config(load_config(args.config))
            except FileNotFoundError:
                policy = exclusion_policy_from_config(None)
            exit_code = 0
            for directory in directories:
                print(f"Checking {directory} …")
                report = probe_scope(directory, policy=policy)
                _print_probe_report(report)
                print()
                if not report.passed:
                    exit_code = 1
            return exit_code

        elif args.command == "panic":
            if not args.yes:
                if not confirm(
                    gui=args.gui,
                    text="Trigger Xenon panic lockdown?",
                ):
                    print("Panic cancelled.")
                    return 0

            config = load_config(args.config)
            if not password_configured(config):
                raise ValueError(
                    "Panic requires a configured password. Set one with: xenon setup"
                )

            password = prompt_password(
                gui=args.gui,
                title="Xenon Panic",
                prompt="Password:",
            )
            if not password:
                raise ValueError("Panic cannot run without a password.")

            run_panic(
                password,
                target=args.target,
                lock_screen=False if args.no_lock_screen else None,
                kill_session=False if args.no_kill_session else None,
                config_file=args.config,
            )

        elif args.command == "install-trigger":
            if args.print_only:
                chord = args.chord
                if chord is None:
                    try:
                        chord = load_config()["trigger"]["chord"]
                    except FileNotFoundError:
                        chord = None
                print(render_trigger(args.desktop, chord=chord), end="")
            else:
                path = install_trigger(
                    args.desktop,
                    chord=args.chord,
                )
                print(f"Installed {args.desktop} trigger: {path}")
                print("Reload your compositor/session if needed.")
                print("Edit ~/.config/xenon/config.json before relying on panic.")

    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return 1

    return 0
