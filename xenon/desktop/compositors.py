from __future__ import annotations

from pathlib import Path

from xenon.config import DEFAULT_CONFIG
from xenon.desktop.chord import (
    Chord,
    niri_bind,
    parse_chord,
    river_map,
    sway_bindsym,
)
from xenon.desktop.command import wrapper_path, write_panic_wrapper
from xenon.desktop.files import (
    MARKER_END,
    MARKER_START,
    ensure_line,
    run_quiet,
    upsert_marked_section,
)


def _resolve_chord(chord: str | Chord | None) -> Chord:
    if isinstance(chord, Chord):
        return chord
    return parse_chord(chord or DEFAULT_CONFIG["trigger"]["chord"])


def _wrapper(path: Path | None) -> Path:
    return path or write_panic_wrapper()


def render_sway(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    return (
        f"{MARKER_START}\n"
        f"bindsym {sway_bindsym(parsed)} exec {wrapper}\n"
        f"{MARKER_END}\n"
    )


def render_i3(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    return render_sway(chord, command=command)


def render_niri(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    bind = niri_bind(parsed)
    return (
        f"// {MARKER_START[2:]}\n"
        "binds {\n"
        f'    {bind} {{ spawn "{wrapper}"; }}\n'
        "}\n"
        f"// {MARKER_END[2:]}\n"
    )


def render_river(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    mods, key = river_map(parsed)
    return (
        f"{MARKER_START}\n"
        f"riverctl map normal {mods} {key} spawn {wrapper!s}\n"
        f"{MARKER_END}\n"
    )


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def install_sway(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    bind_file = Path.home() / ".config" / "sway" / "xenon.conf"
    bind_file.parent.mkdir(parents=True, exist_ok=True)
    bind_file.write_text(render_sway(chord, command=str(wrapper_path)), encoding="utf-8")

    main = _first_existing(
        [
            Path.home() / ".config" / "sway" / "config",
            Path.home() / ".sway" / "config",
        ]
    )
    if main is not None:
        ensure_line(
            main,
            f"include {bind_file}",
            comment="# Project Xenon",
        )
    run_quiet(["swaymsg", "reload"])
    return bind_file


def install_i3(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    bind_file = Path.home() / ".config" / "i3" / "xenon.conf"
    bind_file.parent.mkdir(parents=True, exist_ok=True)
    bind_file.write_text(render_i3(chord, command=str(wrapper_path)), encoding="utf-8")

    main = _first_existing(
        [
            Path.home() / ".config" / "i3" / "config",
            Path.home() / ".i3" / "config",
        ]
    )
    if main is not None:
        ensure_line(
            main,
            f"include {bind_file}",
            comment="# Project Xenon",
        )
    run_quiet(["i3-msg", "reload"])
    return bind_file


def install_niri(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    wrapper = str(wrapper_path)
    bind = niri_bind(parsed)
    body = "binds {\n" f'    {bind} {{ spawn "{wrapper}"; }}\n' "}\n"
    config = Path.home() / ".config" / "niri" / "config.kdl"
    if config.is_file():
        upsert_marked_section(
            config,
            body,
            start="// xenon-panic-start",
            end="// xenon-panic-end",
        )
        return config

    snippet = Path.home() / ".config" / "niri" / "xenon-panic.kdl"
    snippet.parent.mkdir(parents=True, exist_ok=True)
    snippet.write_text(
        "// Project Xenon — paste this binds block into config.kdl\n" + body,
        encoding="utf-8",
    )
    return snippet


def install_river(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    mods, key = river_map(parsed)
    command = f"riverctl map normal {mods} {key} spawn {wrapper_path}"
    init = Path.home() / ".config" / "river" / "init"
    if init.is_file():
        upsert_marked_section(init, command)
        run_quiet(["riverctl", "map", "normal", mods, key, "spawn", str(wrapper_path)])
        return init

    snippet = Path.home() / ".config" / "river" / "xenon-map.sh"
    snippet.parent.mkdir(parents=True, exist_ok=True)
    snippet.write_text(
        "#!/bin/sh\n"
        "# Project Xenon — source this from ~/.config/river/init\n"
        f"{command}\n",
        encoding="utf-8",
    )
    snippet.chmod(0o755)
    run_quiet(["riverctl", "map", "normal", mods, key, "spawn", str(wrapper_path)])
    return snippet
