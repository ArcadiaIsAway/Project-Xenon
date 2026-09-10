from __future__ import annotations

import ast
import shutil
from pathlib import Path

from xenon.config import DEFAULT_CONFIG
from xenon.desktop.chord import Chord, gnome_accel, gtk_accel, kde_shortcut, parse_chord
from xenon.desktop.command import wrapper_path, write_panic_wrapper
from xenon.desktop.files import run_quiet, upsert_marked_section, write_snippet


def _resolve_chord(chord: str | Chord | None) -> Chord:
    if isinstance(chord, Chord):
        return chord
    return parse_chord(chord or DEFAULT_CONFIG["trigger"]["chord"])


def _wrapper(path: Path | None) -> Path:
    return path or write_panic_wrapper()


def render_gnome(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    accel = gnome_accel(parsed)
    return (
        "# GNOME (gsettings) panic hotkey\n"
        f"# command = {wrapper}\n"
        f"# binding = {accel}\n"
    )


def render_kde(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Xenon Panic\n"
        "Comment=Trigger Xenon panic lockdown\n"
        f"Exec={wrapper}\n"
        "NoDisplay=true\n"
        "StartupNotify=false\n"
        f"X-KDE-Shortcuts={kde_shortcut(parsed)}\n"
    )


def render_xfce(chord: str | Chord | None = None, *, command: str | None = None) -> str:
    parsed = _resolve_chord(chord)
    wrapper = command or str(wrapper_path())
    accel = gtk_accel(parsed, control="Primary")
    return (
        "# XFCE panic hotkey\n"
        f'xfconf-query -c xfce4-keyboard-shortcuts -p "/commands/custom/{accel}" '
        f"-n -t string -s {wrapper}\n"
    )


def _parse_gsettings_list(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text or text == "@as []":
        return []
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _gsettings_install(
    *,
    list_schema: str,
    list_key: str,
    item_schema: str,
    item_path: str,
    name: str,
    command: str,
    binding: str,
    binding_is_array: bool,
) -> bool:
    if not shutil.which("gsettings"):
        return False

    listed = run_quiet(["gsettings", "get", list_schema, list_key])
    paths = _parse_gsettings_list(listed.stdout if listed else "")
    if item_path not in paths:
        paths.append(item_path)
        encoded = "[" + ", ".join(f"'{item}'" for item in paths) + "]"
        result = run_quiet(["gsettings", "set", list_schema, list_key, encoded])
        if result is None or result.returncode != 0:
            return False

    reloc = f"{item_schema}:{item_path}"
    binding_value = f"['{binding}']" if binding_is_array else f"'{binding}'"
    steps = [
        ["gsettings", "set", reloc, "name", f"'{name}'"],
        ["gsettings", "set", reloc, "command", f"'{command}'"],
        ["gsettings", "set", reloc, "binding", binding_value],
    ]
    for argv in steps:
        result = run_quiet(argv)
        if result is None or result.returncode != 0:
            return False
    return True


def install_gnome(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    binding = gnome_accel(parsed)
    item_path = (
        "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/xenon-panic/"
    )
    ok = _gsettings_install(
        list_schema="org.gnome.settings-daemon.plugins.media-keys",
        list_key="custom-keybindings",
        item_schema="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",
        item_path=item_path,
        name="Xenon Panic",
        command=str(wrapper_path),
        binding=binding,
        binding_is_array=False,
    )
    snippet = write_snippet(render_gnome(parsed, command=str(wrapper_path)))
    return snippet if not ok else Path(item_path)


def install_cinnamon(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    binding = gnome_accel(parsed)
    item_path = "/org/cinnamon/desktop/keybindings/custom-keybindings/xenon-panic/"
    ok = _gsettings_install(
        list_schema="org.cinnamon.desktop.keybindings",
        list_key="custom-list",
        item_schema="org.cinnamon.desktop.keybindings.custom-keybinding",
        item_path=item_path,
        name="Xenon Panic",
        command=str(wrapper_path),
        binding=binding,
        binding_is_array=True,
    )
    snippet = write_snippet(
        "# Cinnamon panic hotkey\n"
        f"# command = {wrapper_path}\n"
        f"# binding = {binding}\n"
    )
    return snippet if not ok else Path(item_path)


def install_mate(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    binding = gnome_accel(parsed)
    item_path = (
        "/org/mate/desktop/keybindings/custom-keybindings/xenon-panic/"
    )
    ok = _gsettings_install(
        list_schema="org.mate.desktop.keybindings",
        list_key="custom-list",
        item_schema="org.mate.desktop.keybindings.custom-keybinding",
        item_path=item_path,
        name="Xenon Panic",
        command=str(wrapper_path),
        binding=binding,
        binding_is_array=True,
    )
    snippet = write_snippet(
        "# MATE panic hotkey\n"
        f"# command = {wrapper_path}\n"
        f"# binding = {binding}\n"
    )
    return snippet if not ok else Path(item_path)


def install_kde(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    desktop = Path.home() / ".local" / "share" / "applications" / "xenon-panic.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(render_kde(parsed, command=str(wrapper_path)), encoding="utf-8")

    shortcut = kde_shortcut(parsed)
    for tool in ("kwriteconfig6", "kwriteconfig5"):
        if not shutil.which(tool):
            continue
        run_quiet(
            [
                tool,
                "--file",
                "kglobalshortcutsrc",
                "--group",
                "xenon-panic.desktop",
                "--key",
                "_k_friendly_name",
                "Xenon Panic",
            ]
        )
        run_quiet(
            [
                tool,
                "--file",
                "kglobalshortcutsrc",
                "--group",
                "xenon-panic.desktop",
                "--key",
                "_launch",
                f"{shortcut},none,Xenon Panic",
            ]
        )
        break

    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(tool):
            run_quiet([tool])
            break
    return desktop


def install_xfce(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    accel = gtk_accel(parsed, control="Primary")
    property_name = f"/commands/custom/{accel}"
    result = run_quiet(
        [
            "xfconf-query",
            "-c",
            "xfce4-keyboard-shortcuts",
            "-p",
            property_name,
            "-n",
            "-t",
            "string",
            "-s",
            str(wrapper_path),
        ]
    )
    snippet = write_snippet(render_xfce(parsed, command=str(wrapper_path)))
    if result is None or result.returncode != 0:
        return snippet
    return Path(property_name)


def install_lxqt(
    *,
    chord: str | Chord | None = None,
    wrapper: Path | None = None,
) -> Path:
    wrapper_path = _wrapper(wrapper)
    parsed = _resolve_chord(chord)
    shortcut = kde_shortcut(parsed)
    config = Path.home() / ".config" / "lxqt" / "globalkeyshortcuts.conf"
    body = (
        "Comment=Xenon Panic\n"
        "Enabled=true\n"
        f"Exec={wrapper_path}\n"
        f"shortcut={shortcut}\n"
    )
    upsert_marked_section(
        config,
        body,
        start="[xenon-panic]",
        end="; xenon-panic-end",
    )
    return config
