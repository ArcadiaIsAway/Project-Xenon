from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from xenon.exclusions import DEFAULT_OPTIONAL_ENABLED, OPTIONAL_DIR_CATALOG, ExclusionPolicy
from xenon.vault import CIPHER_CHACHA, check_password_verifier, make_password_verifier
from xenon.aggressiveness import DEFAULT_AGGRESSIVENESS, normalize_aggressiveness
from xenon.fx import (
    DEFAULT_ANIMATION,
    DEFAULT_DISPLAY,
    DEFAULT_DURATION,
    DEFAULT_ORDER,
    DEFAULT_PRESET,
    DEFAULT_TTY,
    playback_from_config,
)

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "xenon"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"
STATE_DIR = Path.home() / ".local" / "state" / "xenon"

DEFAULT_CONFIG = {
    "targets": [str(Path.home() / "Documents")],
    "encryption": {
        "cipher": CIPHER_CHACHA,
        "key_file": None,
        "aggressiveness": DEFAULT_AGGRESSIVENESS,
    },
    "exclusions": {
        "protect_system": True,
        "protect_project": True,
        "protect_running": True,
        "optional_dirs": {name: True for name in sorted(DEFAULT_OPTIONAL_ENABLED)},
        "custom_paths": [],
    },
    "lockdown": {
        "enabled": True,
        "backend": "auto",
        "lock_screen": True,
        "kill_session": True,
        "lock_command": None,
        "kill_command": None,
    },
    "trigger": {
        "desktop": None,
        "chord": "SUPER CTRL ALT SHIFT, X",
    },
    "panic": {
        "preset": DEFAULT_PRESET,
        "animation": DEFAULT_ANIMATION,
        "effects": [],
        "duration": DEFAULT_DURATION,
        "display": DEFAULT_DISPLAY,
        "tty": DEFAULT_TTY,
        "music": "",
        "wait_for_music": False,
        "poweroff_after_track": False,
        "script_flash": False,
        "effect_order": DEFAULT_ORDER,
    },
}


def _normalize_target_strings(raw: object) -> list[str]:
    """Deduplicate target path strings while preserving order."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        key = str(Path(text).expanduser())
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def targets_from_config(config: dict) -> list[str]:
    """Return configured encryption targets (legacy ``source`` supported)."""
    targets = _normalize_target_strings(config.get("targets"))
    if targets:
        return targets
    legacy = config.get("source")
    if legacy:
        return _normalize_target_strings([legacy])
    return []


def configured_targets(config: dict | None = None) -> list[Path]:
    """Resolve configured targets to absolute Paths."""
    if config is None:
        config = load_config()
    paths: list[Path] = []
    for raw in targets_from_config(config):
        path = Path(raw).expanduser()
        paths.append(path)
    return paths


def config_path() -> Path:
    env = os.environ.get("XENON_CONFIG")
    if env:
        return Path(env).expanduser()
    local = Path.cwd() / "config.json"
    if local.is_file():
        return local
    return DEFAULT_CONFIG_PATH


def merge_config(data: dict) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    config.update(data)
    config.pop("vault", None)

    # Prefer explicit targets; migrate legacy single ``source``.
    if "targets" in data or "source" in data:
        config["targets"] = targets_from_config(data)
    else:
        config["targets"] = list(DEFAULT_CONFIG["targets"])
    config.pop("source", None)

    encryption = deepcopy(DEFAULT_CONFIG["encryption"])
    encryption.update(data.get("encryption") or {})
    config["encryption"] = encryption

    exclusions = deepcopy(DEFAULT_CONFIG["exclusions"])
    incoming = data.get("exclusions") or {}
    exclusions["protect_system"] = True
    exclusions["protect_project"] = True
    exclusions["protect_running"] = True

    optional = {name: True for name in OPTIONAL_DIR_CATALOG}
    configured = incoming.get("optional_dirs")
    if isinstance(configured, dict):
        for name, enabled in configured.items():
            optional[str(name)] = bool(enabled)
    exclusions["optional_dirs"] = optional

    custom = incoming.get("custom_paths", [])
    exclusions["custom_paths"] = [str(x) for x in custom] if isinstance(custom, list) else []
    config["exclusions"] = exclusions

    lockdown = deepcopy(DEFAULT_CONFIG["lockdown"])
    lockdown.update(data.get("lockdown") or {})
    config["lockdown"] = lockdown

    trigger = deepcopy(DEFAULT_CONFIG["trigger"])
    trigger.update(data.get("trigger") or {})
    config["trigger"] = trigger

    panic = playback_from_config({"panic": {**(DEFAULT_CONFIG["panic"]), **(data.get("panic") or {})}}).as_config()
    config["panic"] = panic
    return config


def load_config(path: Path | None = None) -> dict:
    target = path or config_path()
    if not target.is_file():
        raise FileNotFoundError(
            f"Missing config: {target}\nRun: xenon setup"
        )
    return merge_config(json.loads(target.read_text(encoding="utf-8")))


def save_config(config: dict, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = merge_config(config)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def ensure_config(path: Path | None = None) -> dict:
    target = path or config_path()
    if not target.is_file():
        write_default_config(target, force=True)
    return load_config(target)


def write_default_config(path: Path | None = None, *, force: bool = False) -> Path:
    target = path or DEFAULT_CONFIG_PATH
    if target.exists() and not force:
        raise FileExistsError(f"Config already exists: {target}")
    return save_config(deepcopy(DEFAULT_CONFIG), target)


def password_configured(config: dict | None) -> bool:
    if not config:
        return False
    blob = (config.get("encryption") or {}).get("password")
    return isinstance(blob, dict) and bool(blob.get("hash")) and bool(blob.get("salt"))


def set_config_password(config: dict, password: str) -> None:
    config.setdefault("encryption", {})["password"] = make_password_verifier(password)


def config_password_matches(config: dict | None, password: str) -> bool:
    if not config or not password:
        return False
    blob = (config.get("encryption") or {}).get("password")
    return check_password_verifier(password, blob)


def aggressiveness_from_config(config: dict | None) -> int:
    if not config:
        return DEFAULT_AGGRESSIVENESS
    try:
        return normalize_aggressiveness(
            (config.get("encryption") or {}).get("aggressiveness")
        )
    except ValueError:
        return DEFAULT_AGGRESSIVENESS


def exclusion_policy_from_config(config: dict | None = None) -> ExclusionPolicy:
    if config is None:
        try:
            config = load_config()
        except FileNotFoundError:
            config = DEFAULT_CONFIG
    return ExclusionPolicy.from_config(config)
