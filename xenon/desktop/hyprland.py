from __future__ import annotations

import shutil
import sys
from pathlib import Path

from xenon.config import DEFAULT_CONFIG, write_default_config

HYPR_DIR = Path.home() / ".config" / "hypr"
HYPR_XENON = HYPR_DIR / "xenon.conf"
HYPRLAND_CONF = HYPR_DIR / "hyprland.conf"


def xenon_command() -> str:
    """Resolve an invocation that works after pip install or from a checkout."""
    found = shutil.which("xenon")
    if found:
        return found
    return f"{sys.executable} -m xenon"


def render_bind(chord: str | None = None) -> str:
    chord = chord or DEFAULT_CONFIG["trigger"]["chord"]
    command = xenon_command()
    return (
        "# Project Xenon panic trigger (Hyprland)\n"
        "# Optional desktop hook — not required by the core CLI.\n"
        f"bind = {chord}, exec, {command} panic --gui\n"
    )


def install(
    *,
    chord: str | None = None,
    ensure_config: bool = True,
) -> Path:
    HYPR_DIR.mkdir(parents=True, exist_ok=True)

    if ensure_config:
        try:
            write_default_config()
        except FileExistsError:
            pass

    HYPR_XENON.write_text(render_bind(chord), encoding="utf-8")

    source_line = f"source = {HYPR_XENON}"
    if HYPRLAND_CONF.is_file():
        text = HYPRLAND_CONF.read_text(encoding="utf-8")
        if str(HYPR_XENON) not in text and "hypr/xenon.conf" not in text:
            with HYPRLAND_CONF.open("a", encoding="utf-8") as handle:
                handle.write(f"\n# Project Xenon\n{source_line}\n")
    else:
        HYPRLAND_CONF.write_text(f"{source_line}\n", encoding="utf-8")

    return HYPR_XENON
