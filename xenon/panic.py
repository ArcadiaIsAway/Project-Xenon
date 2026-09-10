from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from xenon.aggressiveness import get_level
from xenon.config import (
    STATE_DIR,
    aggressiveness_from_config,
    configured_targets,
    config_password_matches,
    exclusion_policy_from_config,
    load_config,
    password_configured,
)
from xenon.lockdown import lockdown
from xenon.fx import play_panic_spectacle
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


def panic_lock_plan(
    config: dict,
    password: str,
    *,
    triggered: bool,
) -> tuple[str, int]:
    """Choose password + aggressiveness for a panic run.

    A hotkey (triggered=True) is itself the authorization: no password is
    required. Levels that cannot lock without a secret fall back to
    passwordless encryption so the chord still completes.
    """
    if not triggered:
        if not password:
            raise ValueError("Panic cannot run without a password.")
        if not password_configured(config):
            raise ValueError(
                "Panic requires a configured password. Set one with: xenon setup"
            )
        if not config_password_matches(config, password):
            raise ValueError("Wrong password.")
        return password, aggressiveness_from_config(config)

    if password:
        if password_configured(config) and not config_password_matches(config, password):
            raise ValueError("Wrong password.")
        return password, aggressiveness_from_config(config)

    level = aggressiveness_from_config(config)
    spec = get_level(level)
    if spec.needs_password and not spec.destructive:
        return "", 2
    return "", level


def run_panic(
    password: str = "",
    *,
    triggered: bool = False,
    target: Path | None = None,
    source: Path | None = None,
    directories: list[Path] | None = None,
    lock_screen: bool | None = None,
    kill_session: bool | None = None,
    config: dict | None = None,
    config_file: Path | None = None,
) -> list[Path]:
    if config is None:
        config = load_config(config_file)

    password, level = panic_lock_plan(config, password, triggered=triggered)

    override = target or source
    if override is not None:
        directories = [override.expanduser()]
    elif directories is not None:
        directories = [path.expanduser() for path in directories]
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
    _append_log(f"Panic started: targets={listed} triggered={triggered} level={level}")

    spectacle = play_panic_spectacle(config)
    try:
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
                    aggressiveness=level,
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
    finally:
        if spectacle is not None:
            spectacle.finish()
