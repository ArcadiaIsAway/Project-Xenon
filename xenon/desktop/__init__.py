"""Optional desktop integration helpers.

Core Xenon does not require any compositor. These helpers only generate
or install bindings for environments that want a panic hotkey.
"""

from __future__ import annotations

from xenon.desktop import hyprland


def install_trigger(desktop: str, **kwargs):
    desktop = desktop.lower().strip()
    if desktop == "hyprland":
        return hyprland.install(**kwargs)
    raise ValueError(
        f"Unsupported desktop '{desktop}'. "
        f"Supported: hyprland. See contrib/ for manual snippets."
    )


def render_trigger(desktop: str, **kwargs) -> str:
    desktop = desktop.lower().strip()
    if desktop == "hyprland":
        return hyprland.render_bind(**kwargs)
    raise ValueError(
        f"Unsupported desktop '{desktop}'. "
        f"Supported: hyprland. See contrib/ for manual snippets."
    )
