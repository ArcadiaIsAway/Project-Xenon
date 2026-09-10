from __future__ import annotations

from pathlib import Path

from xenon.config import DEFAULT_CONFIG, write_default_config
from xenon.desktop.chord import Chord, hyprland_bind, parse_chord
from xenon.desktop.command import wrapper_path, write_panic_wrapper
from xenon.desktop.files import ensure_line, run_quiet


def _hypr_dir() -> Path:
    return Path.home() / ".config" / "hypr"


def _hypr_xenon() -> Path:
    return _hypr_dir() / "xenon.conf"


def _hyprland_conf() -> Path:
    return _hypr_dir() / "hyprland.conf"


def _resolve_chord(chord: str | Chord | None) -> Chord:
    if isinstance(chord, Chord):
        return chord
    return parse_chord(chord or DEFAULT_CONFIG["trigger"]["chord"])


def render_bind(
    chord: str | Chord | None = None,
    *,
    command: str | None = None,
) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    return (
        "# Project Xenon panic trigger (Hyprland)\n"
        "# Optional desktop hook — not required by the core CLI.\n"
        "# The wrapper uses an absolute Xenon path so compositor PATH does not matter.\n"
        f"bind = {hyprland_bind(parsed)}, exec, {wrapper}\n"
    )


def install(
    *,
    chord: str | Chord | None = None,
    ensure_config: bool = True,
    wrapper: Path | None = None,
) -> Path:
    hypr_dir = _hypr_dir()
    hypr_xenon = _hypr_xenon()
    hyprland_conf = _hyprland_conf()
    hypr_dir.mkdir(parents=True, exist_ok=True)

    if ensure_config:
        try:
            write_default_config()
        except FileExistsError:
            pass

    wrapper_file = wrapper or write_panic_wrapper()
    hypr_xenon.write_text(
        render_bind(chord, command=str(wrapper_file)),
        encoding="utf-8",
    )

    source_line = f"source = {hypr_xenon}"
    if hyprland_conf.is_file():
        text = hyprland_conf.read_text(encoding="utf-8")
        if str(hypr_xenon) not in text and "hypr/xenon.conf" not in text:
            ensure_line(
                hyprland_conf,
                source_line,
                comment="# Project Xenon",
            )
    else:
        hyprland_conf.write_text(f"{source_line}\n", encoding="utf-8")

    run_quiet(["hyprctl", "reload"])
    return hypr_xenon
