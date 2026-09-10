from __future__ import annotations

import os
import shutil


def detect_desktop() -> str:
    """Identify the running session so a panic hotkey can be installed natively."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("NIRI_SOCKET"):
        return "niri"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("I3SOCK"):
        return "i3"
    if os.environ.get("RIVER_WAYLAND_DISPLAY"):
        return "river"

    desktop = ",".join(
        filter(
            None,
            (
                os.environ.get("XDG_CURRENT_DESKTOP"),
                os.environ.get("XDG_SESSION_DESKTOP"),
                os.environ.get("DESKTOP_SESSION"),
            ),
        )
    ).lower()

    checks = (
        ("hyprland", "hyprland"),
        ("niri", "niri"),
        ("sway", "sway"),
        ("i3", "i3"),
        ("kde", "kde"),
        ("plasma", "kde"),
        ("gnome", "gnome"),
        ("unity", "gnome"),
        ("xfce", "xfce"),
        ("cinnamon", "cinnamon"),
        ("mate", "mate"),
        ("lxqt", "lxqt"),
        ("river", "river"),
    )
    for needle, name in checks:
        if needle in desktop:
            return name

    if shutil.which("hyprctl") and os.environ.get("WAYLAND_DISPLAY"):
        return "hyprland"
    if shutil.which("swaymsg") and os.environ.get("SWAYSOCK"):
        return "sway"
    return "unknown"
