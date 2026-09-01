from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from xenon.config import (
    STATE_DIR,
    configured_targets,
    exclusion_policy_from_config,
    load_config,
)
from xenon.lockdown import lockdown
from xenon.vault import CIPHER_CHACHA, is_locked, load_optional_key_file, lock_directory


def _panic_log_path() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "panic.log"


def _append_log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{stamp}] {message}\n"
    print(message)
    try:
        with _panic_log_path().open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def run_panic(
    password: str,
    *,
    target: Path | None = None,
    source: Path | None = None,
    lock_screen: bool | None = None,
    kill_session: bool | None = None,
    config_file: Path | None = None,
) -> list[Path]:
    config = load_config(config_file)
    override = target or source
    if override is not None:
        directories = [override.expanduser()]
    else:
        directories = configured_targets(config)
        if not directories:
            raise ValueError("No encryption targets configured. Run: xenon setup")

    lockdown_cfg = config["lockdown"]

    if not lockdown_cfg.get("enabled", True):
        lock_screen_enabled = False
        kill_session_enabled = False
    else:
        lock_screen_enabled = (
            lockdown_cfg["lock_screen"]
            if lock_screen is None
            else lock_screen
        )
        kill_session_enabled = (
            lockdown_cfg["kill_session"]
            if kill_session is None
            else kill_session
        )

    backend = lockdown_cfg.get("backend", "auto")
    lock_command = lockdown_cfg.get("lock_command")
    kill_command = lockdown_cfg.get("kill_command")

    listed = ", ".join(str(path) for path in directories)
    _append_log(f"Panic started: targets={listed}")

    if lock_screen_enabled:
        lockdown(
            backend=backend,
            lock_screen=True,
            kill_session=False,
            lock_command=lock_command,
            kill_command=kill_command,
            log=_append_log,
        )

    encryption = config.get("encryption") or {}
    cipher_name = encryption.get("cipher") or CIPHER_CHACHA
    key_file = load_optional_key_file(encryption.get("key_file"))
    policy = exclusion_policy_from_config(config)

    try:
        for directory in directories:
            if is_locked(directory):
                _append_log(f"Already locked: {directory}")
                continue
            lock_directory(
                directory,
                password,
                cipher_name=cipher_name,
                key_file=key_file,
                policy=policy,
            )
            _append_log(f"Directory locked in place: {directory}")
    except Exception as exc:
        _append_log(f"Lock failed: {exc}")
        if kill_session_enabled:
            lockdown(
                backend=backend,
                lock_screen=False,
                kill_session=True,
                lock_command=lock_command,
                kill_command=kill_command,
                log=_append_log,
            )
        raise

    if kill_session_enabled:
        lockdown(
            backend=backend,
            lock_screen=False,
            kill_session=True,
            lock_command=lock_command,
            kill_command=kill_command,
            log=_append_log,
        )

    _append_log("Panic complete.")
    return directories
