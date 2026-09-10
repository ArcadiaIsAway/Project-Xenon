from pathlib import Path

import pytest

from xenon.config import DEFAULT_CONFIG
from xenon.desktop import install_trigger, parse_config_chord, render_trigger, resolve_desktop
from xenon.desktop.chord import gnome_accel, hyprland_bind, parse_chord, sway_bindsym
from xenon.desktop.command import panic_command, write_panic_wrapper
from xenon.desktop.detect import detect_desktop


def test_parse_default_and_canonical_chords():
    legacy = parse_chord(DEFAULT_CONFIG["trigger"]["chord"])
    modern = parse_chord("Super+Ctrl+Alt+Shift+X")
    assert legacy.display() == "Super+Ctrl+Alt+Shift+X"
    assert modern == legacy
    spaced = parse_chord(" Super + Ctrl + Alt + Shift + X ")
    assert spaced == modern


def test_parse_chord_rejects_bare_key():
    with pytest.raises(ValueError, match="modifier"):
        parse_chord("X")
    with pytest.raises(ValueError, match="Unknown modifier"):
        parse_chord("Hyper+X")


def test_chord_backend_renderers():
    chord = parse_chord("Super+Ctrl+Alt+Shift+X")
    assert hyprland_bind(chord) == "SUPER CTRL ALT SHIFT, X"
    assert sway_bindsym(chord) == "Mod4+Ctrl+Alt+Shift+X"
    assert gnome_accel(chord) == "<Super><Control><Alt><Shift>x"


def test_detect_hyprland_and_unknown(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "sig")
    assert detect_desktop() == "hyprland"
    assert resolve_desktop("auto") == "hyprland"

    for key in (
        "HYPRLAND_INSTANCE_SIGNATURE",
        "NIRI_SOCKET",
        "SWAYSOCK",
        "I3SOCK",
        "RIVER_WAYLAND_DISPLAY",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_DESKTOP",
        "DESKTOP_SESSION",
        "WAYLAND_DISPLAY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("xenon.desktop.detect.shutil.which", lambda _name: None)
    assert detect_desktop() == "unknown"


def test_panic_wrapper_uses_absolute_trigger(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_panic_wrapper()
    text = path.read_text(encoding="utf-8")
    assert path == tmp_path / ".local" / "lib" / "xenon" / "panic-trigger"
    assert path.stat().st_mode & 0o111
    assert "panic --trigger" in text
    assert "--gui" not in text
    assert str(path.parent)  # written under HOME
    command = " ".join(panic_command())
    assert "panic --trigger" in command


def test_hyprland_install_binds_wrapper(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XENON_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.setattr("xenon.desktop.hyprland.run_quiet", lambda _argv: None)
    hypr = tmp_path / ".config" / "hypr"
    hypr.mkdir(parents=True)
    (hypr / "hyprland.conf").write_text("# existing config\n", encoding="utf-8")

    installed = install_trigger(
        "hyprland",
        chord="Super+Ctrl+Alt+Shift+X",
        ensure_config=False,
    )
    wrapper = tmp_path / ".local" / "lib" / "xenon" / "panic-trigger"
    bind = installed.read_text(encoding="utf-8")
    assert installed == hypr / "xenon.conf"
    assert str(wrapper) in bind
    assert "panic --gui" not in bind
    assert "bind = SUPER CTRL ALT SHIFT, X, exec," in bind
    main = (hypr / "hyprland.conf").read_text(encoding="utf-8")
    assert str(installed) in main


def test_render_trigger_print_only_mentions_wrapper():
    text = render_trigger(
        "hyprland",
        chord="SUPER CTRL ALT SHIFT, X",
        command="/tmp/xenon-panic-wrapper",
    )
    assert "/tmp/xenon-panic-wrapper" in text
    assert "SUPER CTRL ALT SHIFT, X" in text
    chord = parse_config_chord("SUPER CTRL ALT SHIFT, X")
    assert chord.display() == "Super+Ctrl+Alt+Shift+X"
