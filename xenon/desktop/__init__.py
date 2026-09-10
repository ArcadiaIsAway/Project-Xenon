"""Optional desktop integration helpers.

Core Xenon does not require any compositor. These helpers generate a
PATH-independent panic wrapper and bind it to a keyboard chord.
"""

from __future__ import annotations

from pathlib import Path

from xenon.config import DEFAULT_CONFIG, load_config, save_config
from xenon.desktop import compositors, hyprland, sessions
from xenon.desktop.chord import Chord, parse_chord
from xenon.desktop.command import panic_command, wrapper_path, write_panic_wrapper
from xenon.desktop.detect import detect_desktop
from xenon.desktop.files import write_snippet

SUPPORTED_DESKTOPS = (
    "hyprland",
    "sway",
    "i3",
    "niri",
    "river",
    "gnome",
    "kde",
    "xfce",
    "cinnamon",
    "mate",
    "lxqt",
)


def resolve_desktop(name: str | None = None) -> str:
    if not name or str(name).lower().strip() in {"auto", "detect", "default"}:
        return detect_desktop()
    return str(name).lower().strip()


def parse_config_chord(text: str | None = None) -> Chord:
    return parse_chord(text or DEFAULT_CONFIG["trigger"]["chord"])


def _persist_trigger(desktop: str, chord: Chord, config_file: Path | None = None) -> None:
    try:
        config = load_config(config_file)
    except FileNotFoundError:
        return
    trigger = config.setdefault("trigger", {})
    trigger["desktop"] = desktop
    trigger["chord"] = chord.display()
    save_config(config, config_file)


def render_trigger(
    desktop: str | None = None,
    *,
    chord: str | Chord | None = None,
    command: str | None = None,
) -> str:
    resolved = resolve_desktop(desktop)
    parsed = chord if isinstance(chord, Chord) else parse_config_chord(chord)
    wrapper = command or str(wrapper_path())
    header = (
        f"# Xenon panic wrapper: {wrapper}\n"
        f"# Command: {' '.join(panic_command())}\n"
        f"# Chord: {parsed.display()}\n"
        f"# Desktop: {resolved}\n"
    )
    body = _render_body(resolved, parsed, wrapper)
    return header + body


def _render_body(desktop: str, chord: Chord, wrapper: str) -> str:
    if desktop == "hyprland":
        return hyprland.render_bind(chord, command=wrapper)
    if desktop in {"sway", "i3"}:
        return compositors.render_sway(chord, command=wrapper)
    if desktop == "niri":
        return compositors.render_niri(chord, command=wrapper)
    if desktop == "river":
        return compositors.render_river(chord, command=wrapper)
    if desktop == "gnome":
        return sessions.render_gnome(chord, command=wrapper)
    if desktop == "kde":
        return sessions.render_kde(chord, command=wrapper)
    if desktop == "xfce":
        return sessions.render_xfce(chord, command=wrapper)
    if desktop in {"cinnamon", "mate"}:
        return (
            f"# {desktop} panic hotkey\n"
            f"# command = {wrapper}\n"
            f"# binding = {chord.display()}\n"
        )
    if desktop == "lxqt":
        return (
            "[xenon-panic]\n"
            "Comment=Xenon Panic\n"
            "Enabled=true\n"
            f"Exec={wrapper}\n"
            f"shortcut={chord.display()}\n"
        )
    return (
        "# Desktop was not detected. Bind this command in your compositor/DE:\n"
        f"{wrapper}\n"
    )


def install_trigger(
    desktop: str | None = None,
    *,
    chord: str | Chord | None = None,
    ensure_config: bool = True,
    config_file: Path | None = None,
) -> Path:
    resolved = resolve_desktop(desktop)
    parsed = chord if isinstance(chord, Chord) else parse_config_chord(chord)
    wrapper = write_panic_wrapper()
    write_snippet(render_trigger(resolved, chord=parsed, command=str(wrapper)))

    if resolved == "hyprland":
        path = hyprland.install(
            chord=parsed,
            ensure_config=ensure_config,
            wrapper=wrapper,
        )
    elif resolved == "sway":
        path = compositors.install_sway(chord=parsed, wrapper=wrapper)
    elif resolved == "i3":
        path = compositors.install_i3(chord=parsed, wrapper=wrapper)
    elif resolved == "niri":
        path = compositors.install_niri(chord=parsed, wrapper=wrapper)
    elif resolved == "river":
        path = compositors.install_river(chord=parsed, wrapper=wrapper)
    elif resolved == "gnome":
        path = sessions.install_gnome(chord=parsed, wrapper=wrapper)
    elif resolved == "kde":
        path = sessions.install_kde(chord=parsed, wrapper=wrapper)
    elif resolved == "xfce":
        path = sessions.install_xfce(chord=parsed, wrapper=wrapper)
    elif resolved == "cinnamon":
        path = sessions.install_cinnamon(chord=parsed, wrapper=wrapper)
    elif resolved == "mate":
        path = sessions.install_mate(chord=parsed, wrapper=wrapper)
    elif resolved == "lxqt":
        path = sessions.install_lxqt(chord=parsed, wrapper=wrapper)
    else:
        path = wrapper

    _persist_trigger(resolved, parsed, config_file)
    return path
