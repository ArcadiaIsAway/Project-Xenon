"""Theatrical panic animations, presets, and playback options.

Visuals stay on the current graphical session. Console / TTY-storm modes
are a fake terminal overlay — they never switch virtual terminals.
"""

from __future__ import annotations

import json
import math
import os
import random
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable


DEFAULT_ANIMATION = "none"
DEFAULT_DURATION = 2.5
DEFAULT_PRESET = "none"
DEFAULT_DISPLAY = "overlay"
DEFAULT_TTY = "auto"
DEFAULT_ORDER = "together"
MIN_DURATION = 0.8
MAX_DURATION = 30.0
MUSIC_WAIT_CAP = 180.0


@dataclass(frozen=True)
class Animation:
    key: str
    title: str
    summary: str


@dataclass(frozen=True)
class Effect:
    key: str
    title: str
    summary: str


@dataclass
class Playback:
    preset: str = DEFAULT_PRESET
    animation: str = DEFAULT_ANIMATION
    effects: list[str] = field(default_factory=list)
    duration: float = DEFAULT_DURATION
    display: str = DEFAULT_DISPLAY
    tty: str = DEFAULT_TTY
    music: str = ""
    wait_for_music: bool = False
    poweroff_after_track: bool = False
    script_flash: bool = False
    effect_order: str = DEFAULT_ORDER

    def as_config(self) -> dict:
        payload = asdict(self)
        payload["effects"] = list(self.effects)
        return payload


@dataclass
class SpectacleRun:
    processes: list[subprocess.Popen] = field(default_factory=list)
    restore: list[Callable[[], None]] = field(default_factory=list)
    wait_for_music: bool = False
    poweroff_after_track: bool = False
    preview: bool = False
    duration: float = DEFAULT_DURATION

    def finish(self) -> None:
        deadline = time.monotonic() + (
            MUSIC_WAIT_CAP if self.wait_for_music else self.duration
        )
        _wait_run_processes(self, deadline, skip_on_enter=self.preview)
        for fn in reversed(self.restore):
            try:
                fn()
            except Exception:
                pass
        if self.preview:
            return
        if self.poweroff_after_track:
            _poweroff()


def _wait_run_processes(
    run: "SpectacleRun",
    deadline: float,
    *,
    skip_on_enter: bool,
) -> None:
    while run.processes:
        alive = [proc for proc in run.processes if proc.poll() is None]
        if not alive:
            return
        if skip_on_enter and _skip_key_pressed():
            for proc in alive:
                proc.terminate()
            return
        if time.monotonic() >= deadline:
            for proc in alive:
                proc.terminate()
            return
        remaining = max(0.05, min(0.12, deadline - time.monotonic()))
        try:
            alive[0].wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            continue


def _skip_key_pressed() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return False
    if not ready:
        return False
    try:
        data = os.read(sys.stdin.fileno(), 64)
    except OSError:
        return False
    return any(marker in data for marker in (b"\n", b"\r", b"\x1b"))


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    summary: str
    playback: Playback


ANIMATIONS: tuple[Animation, ...] = (
    Animation("none", "None", "No overlay — lock immediately."),
    Animation("matrix", "Matrix", "Green code rain while targets seal."),
    Animation("glitch", "Glitch", "RGB tear and scanlines."),
    Animation("bsod", "Kernel panic", "Fake fatal error screen."),
    Animation("redalert", "Red alert", "Pulsing alarm flood."),
    Animation("static", "Static", "Dead-channel television noise."),
    Animation("nuke", "Countdown", "3-2-1 then a white flash."),
    Animation("typewriter", "Protocol", "Cinematic lockdown log."),
)

EFFECTS: tuple[Effect, ...] = (
    Effect("flash", "Flash", "Strobe the overlay at the start."),
    Effect("sound", "Sound", "Built-in siren if no music file is set."),
    Effect("notify", "Notification", "Desktop notification."),
    Effect("shake", "Shake", "Hyprland cursor zoom pulse, if running."),
)

DISPLAY_MODES = (
    ("overlay", "Overlay", "Fullscreen window on the current session."),
    ("tty", "Console look", "Fake TTY overlay — does not switch virtual terminals."),
    ("both", "Both", "Animation plus console chrome on this session."),
)

EFFECT_ORDERS = (
    ("together", "Together", "Effects and animation at the same time."),
    ("effects-first", "Effects first", "Siren/flash/notify, then animation."),
    ("animation-first", "Animation first", "Animation, then remaining effects."),
)

PRESETS: tuple[Preset, ...] = (
    Preset(
        "none",
        "None",
        "No spectacle — seal immediately.",
        Playback(preset="none"),
    ),
    Preset(
        "silent",
        "Silent",
        "Matrix overlay, no sound.",
        Playback(preset="silent", animation="matrix", display="overlay", duration=3.0),
    ),
    Preset(
        "cinematic",
        "Cinematic",
        "Protocol overlay; waits for your music if set.",
        Playback(
            preset="cinematic",
            animation="typewriter",
            effects=["notify"],
            display="overlay",
            duration=4.0,
            wait_for_music=True,
        ),
    ),
    Preset(
        "rave",
        "Rave",
        "Glitch, strobe, siren, shake.",
        Playback(
            preset="rave",
            animation="glitch",
            effects=["flash", "sound", "shake"],
            display="overlay",
            duration=3.0,
        ),
    ),
    Preset(
        "tty-storm",
        "TTY storm",
        "Fake console overlay that flashes Xenon scripts as they run.",
        Playback(
            preset="tty-storm",
            animation="matrix",
            effects=["flash"],
            display="tty",
            tty="auto",
            script_flash=True,
            duration=5.0,
        ),
    ),
    Preset(
        "finale",
        "Finale",
        "Countdown, music, then power off after the track.",
        Playback(
            preset="finale",
            animation="nuke",
            effects=["sound", "flash"],
            display="both",
            wait_for_music=True,
            poweroff_after_track=True,
            duration=5.0,
        ),
    ),
)

ANIMATION_BY_KEY = {item.key: item for item in ANIMATIONS}
EFFECT_BY_KEY = {item.key: item for item in EFFECTS}
PRESET_BY_KEY = {item.key: item for item in PRESETS}
DISPLAY_BY_KEY = {key: (title, summary) for key, title, summary in DISPLAY_MODES}
ORDER_BY_KEY = {key: (title, summary) for key, title, summary in EFFECT_ORDERS}


def normalize_animation(value: object, *, strict: bool = False) -> str:
    if value is None or value == "":
        return DEFAULT_ANIMATION
    key = str(value).strip().lower()
    aliases = {"off": "none", "kernel": "bsod", "alert": "redalert"}
    key = aliases.get(key, key)
    if key in ANIMATION_BY_KEY:
        return key
    if strict:
        raise ValueError(f"Unknown panic animation: {value!r}")
    return DEFAULT_ANIMATION


def normalize_effects(value: object, *, strict: bool = False) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item).strip() for item in value]
    else:
        if strict:
            raise ValueError(f"Invalid panic effects: {value!r}")
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = item.lower()
        if not key:
            continue
        if key not in EFFECT_BY_KEY:
            if strict:
                raise ValueError(f"Unknown panic effect: {item!r}")
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def normalize_duration(value: object) -> float:
    if value is None or value == "":
        return DEFAULT_DURATION
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DURATION
    return max(MIN_DURATION, min(MAX_DURATION, duration))


def normalize_preset(value: object, *, strict: bool = False) -> str:
    if value is None or value == "":
        return DEFAULT_PRESET
    key = str(value).strip().lower()
    aliases = {"off": "none", "hack": "tty-storm", "shutdown": "finale"}
    key = aliases.get(key, key)
    if key == "custom" or key in PRESET_BY_KEY:
        return key
    if strict:
        raise ValueError(f"Unknown panic preset: {value!r}")
    return DEFAULT_PRESET


def normalize_display(value: object) -> str:
    key = str(value or DEFAULT_DISPLAY).strip().lower()
    return key if key in DISPLAY_BY_KEY else DEFAULT_DISPLAY


def normalize_tty(value: object) -> str:
    raw = str(value or DEFAULT_TTY).strip().lower()
    if raw in {"", "auto", "current"}:
        return raw or DEFAULT_TTY
    if raw.startswith("/dev/tty"):
        return raw
    if raw.isdigit() and 1 <= int(raw) <= 63:
        return raw
    return DEFAULT_TTY


def normalize_order(value: object) -> str:
    key = str(value or DEFAULT_ORDER).strip().lower().replace(" ", "-")
    return key if key in ORDER_BY_KEY else DEFAULT_ORDER


def normalize_music(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser())


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def apply_preset(key: str, *, music: str = "") -> Playback:
    preset = PRESET_BY_KEY.get(normalize_preset(key))
    if preset is None:
        playback = Playback(preset="custom", music=music)
    else:
        playback = Playback(**{**preset.playback.as_config(), "music": music})
        playback.preset = preset.key
    return playback


def playback_from_config(config: dict | None) -> Playback:
    blob = (config or {}).get("panic") or {}
    return Playback(
        preset=normalize_preset(blob.get("preset")),
        animation=normalize_animation(blob.get("animation")),
        effects=normalize_effects(blob.get("effects")),
        duration=normalize_duration(blob.get("duration")),
        display=normalize_display(blob.get("display")),
        tty=normalize_tty(blob.get("tty")),
        music=normalize_music(blob.get("music")),
        wait_for_music=_as_bool(blob.get("wait_for_music")),
        poweroff_after_track=_as_bool(blob.get("poweroff_after_track")),
        script_flash=_as_bool(blob.get("script_flash")),
        effect_order=normalize_order(blob.get("effect_order")),
    )


def fx_from_config(config: dict | None) -> tuple[str, list[str], float]:
    playback = playback_from_config(config)
    return playback.animation, playback.effects, playback.duration


def describe_fx(config: dict | None) -> str:
    playback = playback_from_config(config)
    if playback.preset not in {"none", "custom"} and playback.preset in PRESET_BY_KEY:
        title = PRESET_BY_KEY[playback.preset].title
    else:
        title = ANIMATION_BY_KEY[playback.animation].title
    extras: list[str] = []
    if playback.music:
        extras.append("music")
    if playback.display != "overlay":
        extras.append(playback.display)
    if playback.script_flash:
        extras.append("script flash")
    if playback.poweroff_after_track:
        extras.append("poweroff")
    if extras:
        return f"{title} · " + ", ".join(extras)
    return title


def preview_panic_spectacle(config: dict | None) -> SpectacleRun:
    """Play configured effects only. Never locks, wipes, or powers off."""
    return play_panic_spectacle(config, preview=True)


def play_panic_spectacle(config: dict | None, *, preview: bool = False) -> SpectacleRun:
    """Start the spectacle. Call ``.finish()`` after locking (or after preview)."""
    run = SpectacleRun(preview=preview)
    if os.environ.get("PYTEST_CURRENT_TEST") and not preview:
        return run
    if os.environ.get("XENON_NO_FX") == "1":
        return run

    playback = playback_from_config(config)
    run.duration = playback.duration
    # Preview is a dry-run of the spectacle: never schedule shutdown.
    run.poweroff_after_track = False if preview else playback.poweroff_after_track
    idle = (
        playback.animation == "none"
        and not playback.effects
        and not playback.music
        and not playback.script_flash
        and playback.display == "overlay"
    )
    if idle:
        if preview:
            run.finish()
        return run

    try:
        _start_spectacle(playback, run)
        run.duration = playback.duration
    except Exception:
        if preview:
            raise
        run.finish()
        return SpectacleRun(preview=preview)
    if preview:
        run.finish()
    return run


def _start_spectacle(playback: Playback, run: SpectacleRun) -> None:
    music_proc = _start_music(playback)
    if music_proc is not None:
        run.processes.append(music_proc)
        run.wait_for_music = playback.wait_for_music
        run.duration = playback.duration

    def effects() -> None:
        run.restore.extend(_run_side_effects(playback))

    def visuals() -> None:
        _start_visuals(playback, run)

    order = playback.effect_order
    if order == "effects-first":
        effects()
        visuals()
    elif order == "animation-first":
        visuals()
        effects()
    else:
        effects()
        visuals()


def _run_side_effects(playback: Playback) -> list[Callable[[], None]]:
    restore: list[Callable[[], None]] = []
    if "notify" in playback.effects:
        _effect_notify(playback.animation)
    if "sound" in playback.effects and not playback.music:
        restore.extend(_effect_sound())
    if "shake" in playback.effects:
        restore.extend(_effect_shake())
    if "flash" in playback.effects and playback.animation == "none":
        _effect_flash_hypr()
    return restore


def _console_look(playback: Playback) -> bool:
    return playback.display in {"tty", "both"} or playback.script_flash


def parse_geometry(value: object) -> tuple[int, int] | None:
    text = str(value or "").strip().lower()
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width, height = int(left), int(right)
    except ValueError:
        return None
    if width < 320 or height < 240:
        return None
    return width, height


def _screen_size(forced: tuple[int, int] | None = None) -> tuple[int, int]:
    if forced is not None:
        return forced
    hypr = _hypr_monitor_size()
    if hypr is not None:
        return hypr
    if os.environ.get("PYTEST_CURRENT_TEST"):
        cols, rows = shutil.get_terminal_size((80, 24))
        return max(640, cols * 8), max(360, rows * 16)
    try:
        tk = _load_tk()
        if tk is not None:
            probe = tk.Tk()
            probe.withdraw()
            width, height = int(probe.winfo_screenwidth()), int(probe.winfo_screenheight())
            probe.destroy()
            if width >= 320 and height >= 240:
                return width, height
    except Exception:
        pass
    cols, rows = shutil.get_terminal_size((80, 24))
    return max(640, cols * 8), max(360, rows * 16)


def _hypr_monitor_size() -> tuple[int, int] | None:
    if not shutil.which("hyprctl"):
        return None
    try:
        result = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            check=False,
            capture_output=True,
            text=True,
        )
        monitors = json.loads(result.stdout or "[]")
    except Exception:
        return None
    if not isinstance(monitors, list) or not monitors:
        return None
    focused = next((item for item in monitors if item.get("focused")), monitors[0])
    try:
        width = int(focused.get("width") or 0)
        height = int(focused.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width < 320 or height < 240:
        return None
    return width, height


def _visual_argv(playback: Playback, *, ansi: bool, preview: bool = False) -> list[str]:
    width, height = _screen_size()
    argv = [
        sys.executable,
        "-m",
        "xenon.fx",
        "--animation",
        playback.animation,
        "--duration",
        str(playback.duration),
        "--geometry",
        f"{width}x{height}",
    ]
    if "flash" in playback.effects:
        argv.append("--flash")
    if playback.script_flash:
        argv.append("--script-flash")
    if _console_look(playback):
        argv.append("--console")
    if preview:
        argv.append("--preview")
    if ansi:
        argv.append("--ansi")
    return argv


def _start_visuals(playback: Playback, run: SpectacleRun) -> None:
    console = _console_look(playback)
    if playback.animation == "none" and not console:
        if "flash" in playback.effects:
            _effect_flash_hypr()
        return

    if run.preview and _play_overlay_here(playback, preview=True):
        return

    proc = _spawn_overlay_process(playback, preview=run.preview)
    if proc is not None:
        run.processes.append(proc)
        return
    if run.preview and sys.stdout.isatty():
        _play_ansi(
            playback.animation,
            playback.duration,
            flash="flash" in playback.effects,
            script_flash=playback.script_flash,
            console=console,
            preview=True,
        )
        return
    if "flash" in playback.effects:
        _effect_flash_hypr()


def _play_overlay_here(playback: Playback, *, preview: bool) -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    tk = _load_tk()
    if tk is None:
        return False
    return _play_tk(
        tk,
        playback.animation,
        playback.duration,
        flash="flash" in playback.effects,
        script_flash=playback.script_flash,
        console=_console_look(playback),
        preview=preview,
        screen=_screen_size(),
    )


def _spawn_overlay_process(
    playback: Playback,
    *,
    preview: bool = False,
) -> subprocess.Popen | None:
    argv = _visual_argv(playback, ansi=False, preview=preview)
    try:
        return subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except OSError:
        return _spawn_terminal_process(_visual_argv(playback, ansi=True, preview=preview))


def _spawn_terminal_process(argv: list[str]) -> subprocess.Popen | None:
    width, height = _screen_size()
    cols = max(80, width // 8)
    rows = max(24, height // 16)
    launches = (
        [
            "kitty",
            "--start-as=fullscreen",
            "-o",
            "remember_window_size=no",
            "-o",
            f"initial_window_width={width}",
            "-o",
            f"initial_window_height={height}",
            "-e",
        ],
        [
            "alacritty",
            "-o",
            "window.startup_mode=Fullscreen",
            "-o",
            f"window.dimensions.columns={cols}",
            "-o",
            f"window.dimensions.lines={rows}",
            "-e",
        ],
        ["foot", "--fullscreen", "-W", f"{width}x{height}", "-e"],
        ["wezterm", "start", "--fullscreen", "--"],
        ["ghostty", "--fullscreen", "-e"],
        ["konsole", "--fullscreen", "-e"],
        ["gnome-terminal", "--full-screen", "--"],
        ["xterm", "-fullscreen", "-geometry", f"{cols}x{rows}", "-e"],
    )
    for prefix in launches:
        if not shutil.which(prefix[0]):
            continue
        try:
            return subprocess.Popen(
                [*prefix, *argv],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            continue
    return None


def _start_music(playback: Playback) -> subprocess.Popen | None:
    path = Path(playback.music).expanduser() if playback.music else None
    if path is None or not path.is_file():
        return None
    argv = _music_argv(path)
    if argv is None:
        return None
    probed = _probe_audio_seconds(path)
    if probed and playback.wait_for_music:
        playback.duration = max(playback.duration, min(MUSIC_WAIT_CAP, probed))
    try:
        return subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None


def _music_argv(path: Path) -> list[str] | None:
    if shutil.which("mpv"):
        return ["mpv", "--no-video", "--really-quiet", str(path)]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
    if shutil.which("pw-play"):
        return ["pw-play", str(path)]
    if shutil.which("paplay"):
        return ["paplay", str(path)]
    if path.suffix.lower() in {".wav", ".au"} and shutil.which("aplay"):
        return ["aplay", "-q", str(path)]
    return None


def _probe_audio_seconds(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "r") as wav:
                rate = float(wav.getframerate() or 1)
                return wav.getnframes() / rate
        except Exception:
            pass
    if shutil.which("ffprobe"):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            return float((result.stdout or "").strip())
        except ValueError:
            return None
    return None


def _poweroff() -> None:
    for argv in (
        ["systemctl", "poweroff"],
        ["loginctl", "poweroff"],
        ["shutdown", "-h", "now"],
    ):
        if shutil.which(argv[0]):
            subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return


def _load_tk():
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        return None
    try:
        import tkinter as tk
    except Exception:
        return None
    return tk


def _play_tk(
    tk,
    animation: str,
    duration: float,
    *,
    flash: bool,
    script_flash: bool = False,
    console: bool = False,
    preview: bool = False,
    screen: tuple[int, int] | None = None,
) -> bool:
    sw, sh = _screen_size(screen)
    root = tk.Tk()
    root.title("Xenon Panic")
    root.configure(bg="#000000")
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    root.geometry(f"{sw}x{sh}+0+0")
    root.minsize(sw, sh)
    try:
        root.attributes("-fullscreen", True)
    except tk.TclError:
        pass
    try:
        root.state("zoomed")
    except tk.TclError:
        pass
    root.configure(cursor="none")
    canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg="#000000")
    canvas.pack(fill="both", expand=True)
    try:
        root.update_idletasks()
        root.update()
    except tk.TclError:
        pass
    started = time.monotonic()
    state: dict = {"frame": 0, "cursor": 0, "size": (sw, sh)}

    def dismiss(_event=None) -> str:
        try:
            root.destroy()
        except Exception:
            pass
        return "break"

    root.bind("<Escape>", dismiss)
    root.bind("<Return>", dismiss)
    root.bind("<KP_Enter>", dismiss)
    canvas.bind("<Return>", dismiss)
    canvas.bind("<Escape>", dismiss)
    if preview:
        root.bind("<space>", dismiss)
    try:
        root.focus_force()
        root.focus_set()
        canvas.focus_set()
    except tk.TclError:
        pass

    def view_size() -> tuple[int, int]:
        try:
            cw = int(canvas.winfo_width())
            ch = int(canvas.winfo_height())
            rw = int(root.winfo_width())
            rh = int(root.winfo_height())
            tw = int(root.winfo_screenwidth())
            th = int(root.winfo_screenheight())
        except tk.TclError:
            return state["size"]
        width = max(sw, cw, rw, tw, 320)
        height = max(sh, ch, rh, th, 240)
        return width, height

    def tick() -> None:
        try:
            if not root.winfo_exists():
                return
        except tk.TclError:
            return
        elapsed = time.monotonic() - started
        if elapsed >= duration:
            dismiss()
            return
        progress = min(1.0, elapsed / max(duration, 0.01))
        width, height = view_size()
        if (width, height) != state["size"]:
            state["size"] = (width, height)
            state.pop("cols", None)
        canvas.delete("all")
        invert = script_flash and (state["frame"] % 8) < 2
        if flash and elapsed < 0.18:
            canvas.configure(bg="#ffffff")
            canvas.create_rectangle(0, 0, width, height, fill="#ffffff", outline="")
        elif console or script_flash:
            _paint_console_tk(
                canvas,
                animation,
                progress,
                width,
                height,
                state,
                script_flash=script_flash,
                invert=invert,
                preview=preview,
            )
        else:
            _paint_tk(canvas, animation, progress, width, height, state, preview=preview)
        state["frame"] += 1
        try:
            root.after(32, tick)
        except tk.TclError:
            return

    root.after(16, tick)
    try:
        root.mainloop()
    except Exception:
        dismiss()
        return False
    return True


def _mono(height: int, fraction: float, *, bold: bool = False, minimum: int = 10) -> tuple:
    size = max(minimum, int(height * fraction))
    return ("monospace", size, "bold") if bold else ("monospace", size)


def _paint_console_tk(
    canvas,
    animation: str,
    t: float,
    w: int,
    h: int,
    state: dict,
    *,
    script_flash: bool,
    invert: bool,
    preview: bool = False,
) -> None:
    bg = "#33ff33" if invert else "#050805"
    fg = "#050805" if invert else "#33ff33"
    dim = "#020202" if invert else "#1a5a1a"
    bar = "#c8c8c8" if invert else "#111111"
    canvas.configure(bg=bg)
    canvas.create_rectangle(0, 0, w, h, fill=bg, outline="")
    bar_h = max(28, h // 36)
    row_h = max(14, h // 48)
    font = _mono(h, 0.018, minimum=11)
    header_font = _mono(h, 0.016, minimum=10)
    chars = max(20, int(w / max(7, row_h * 0.62)))
    canvas.create_rectangle(0, 0, w, bar_h, fill=bar, outline="")
    canvas.create_text(
        12,
        bar_h / 2,
        text="tty3  linux  xenon-panic  (visual console — session stays here)",
        fill="#888888" if not invert else "#222222",
        font=header_font,
        anchor="w",
    )
    top = bar_h + 8
    usable = max(1, (h - top - (36 if preview else 16)) // row_h)
    if script_flash:
        lines = state.setdefault("scripts", _script_lines())
        cursor = int(state.get("cursor", 0))
        for index in range(usable):
            line = lines[(cursor + index) % len(lines)]
            canvas.create_text(
                12,
                top + index * row_h,
                text=line[:chars],
                fill=fg,
                font=font,
                anchor="nw",
            )
        state["cursor"] = cursor + max(1, usable // 8)
    else:
        dmesg = state.setdefault("dmesg", _console_dmesg(animation))
        shown = min(usable - 2, max(4, int(len(dmesg) * min(1.0, t * 1.8))))
        for index, line in enumerate(dmesg[:shown]):
            canvas.create_text(
                12,
                top + index * row_h,
                text=line[:chars],
                fill=dim if line.startswith("[") else fg,
                font=font,
                anchor="nw",
            )
        cursor = "█" if int(t * 8) % 2 == 0 else " "
        canvas.create_text(
            12,
            top + shown * row_h,
            text=f"xenon-panic login:{cursor}",
            fill=fg,
            font=font,
            anchor="nw",
        )
    if preview:
        canvas.create_text(
            w / 2,
            h - 18,
            text="Enter or Esc to exit preview",
            fill=dim,
            font=header_font,
        )


def _console_dmesg(animation: str) -> list[str]:
    title = ANIMATION_BY_KEY.get(animation, ANIMATION_BY_KEY["none"]).title
    return [
        "[    0.000000] Linux version xenon-panic (visual tty3)",
        "[    0.000012] Command line: xenon.panic spectacle=1 vt.handoff=0",
        "[    0.041882] xenon: staying on the current graphical session",
        f"[    0.104220] xenon: animation={title.lower()}",
        "[    0.188441] xenon: sealing encryption targets",
        "[    0.250102] xenon: console dump is simulated — no VT switch",
        "xenon-panic tty3",
        "",
        "Xenon Panic console",
        "This is a visual TTY. The graphical session is still active.",
    ]


def _paint_tk(
    canvas,
    animation: str,
    t: float,
    w: int,
    h: int,
    state: dict,
    preview: bool = False,
) -> None:
    if animation == "matrix":
        canvas.configure(bg="#020401")
        canvas.create_rectangle(0, 0, w, h, fill="#020401", outline="")
        columns = state.setdefault("cols", _matrix_columns(w, h))
        for col in columns:
            col["y"] = (col["y"] + col["speed"]) % (h + 80)
            for trail, ch in enumerate(col["glyphs"]):
                y = col["y"] - trail * max(16, h // 60)
                if y < 0 or y > h:
                    continue
                color = "#d7ffd7" if trail == 0 else f"#00{max(40, 180 - trail * 18):02x}20"
                canvas.create_text(
                    col["x"],
                    y,
                    text=ch,
                    fill=color,
                    font=_mono(h, 0.018, minimum=12),
                )
        _banner(canvas, w, h, "SEALING", "#7CFF7C")
        _preview_hint(canvas, w, h, preview)
        return

    if animation == "glitch":
        canvas.configure(bg="#050308")
        canvas.create_rectangle(0, 0, w, h, fill="#050308", outline="")
        rng = random.Random(state["frame"] * 17 + 3)
        blobs = max(40, (w * h) // 35000)
        for _ in range(blobs):
            x = rng.randint(0, max(1, w - 40))
            y = rng.randint(0, max(1, h - 10))
            color = rng.choice(("#ff2bd6", "#2bfff8", "#ffffff", "#7a00ff"))
            canvas.create_rectangle(
                x,
                y,
                x + rng.randint(max(20, w // 80), max(80, w // 12)),
                y + rng.randint(2, max(8, h // 80)),
                fill=color,
                outline="",
            )
        jitter = int(math.sin(t * 40) * max(12, w // 90))
        canvas.create_text(
            w / 2 + jitter,
            h / 2,
            text="XENON",
            fill="#ff4ad8",
            font=_mono(h, 0.09, bold=True, minimum=36),
        )
        canvas.create_text(
            w / 2 - jitter,
            h / 2 + 8,
            text="XENON",
            fill="#4af0ff",
            font=_mono(h, 0.09, bold=True, minimum=36),
        )
        _preview_hint(canvas, w, h, preview)
        return

    if animation == "bsod":
        canvas.configure(bg="#0734ae")
        canvas.create_rectangle(0, 0, w, h, fill="#0734ae", outline="")
        lines = [
            ":-(",
            "Your PC ran into a problem that it doesn't want you to see.",
            "Xenon is sealing encryption targets.",
            f"Stop code: XENON_PANIC_{int(t * 99):02d}",
            "Do not close this overlay.",
        ]
        canvas.create_text(
            max(48, w // 24),
            max(80, h // 8),
            text=lines[0],
            fill="#ffffff",
            font=_mono(h, 0.11, minimum=48),
            anchor="w",
        )
        y = max(200, h // 3)
        for line in lines[1:]:
            canvas.create_text(
                max(48, w // 24),
                y,
                text=line,
                fill="#d6e4ff",
                font=_mono(h, 0.028, minimum=16),
                anchor="w",
            )
            y += max(32, h // 28)
        canvas.create_rectangle(
            max(48, w // 24),
            h - max(70, h // 14),
            max(48, w // 24) + int((w - 160) * min(1.0, t * 1.2)),
            h - max(58, h // 16),
            fill="#ffffff",
            outline="",
        )
        _preview_hint(canvas, w, h, preview)
        return

    if animation == "redalert":
        pulse = 0.35 + 0.65 * abs(math.sin(t * 18))
        shade = int(40 + pulse * 160)
        fill = f"#{shade:02x}0000"
        canvas.configure(bg=fill)
        canvas.create_rectangle(0, 0, w, h, fill=fill, outline="")
        canvas.create_text(
            w / 2,
            h / 2 - max(24, h // 24),
            text="RED ALERT",
            fill="#ffffff",
            font=_mono(h, 0.08, bold=True, minimum=36),
        )
        canvas.create_text(
            w / 2,
            h / 2 + max(28, h // 22),
            text="XENON PANIC",
            fill="#ffd0d0",
            font=_mono(h, 0.032, minimum=18),
        )
        _preview_hint(canvas, w, h, preview)
        return

    if animation == "static":
        canvas.configure(bg="#000000")
        canvas.create_rectangle(0, 0, w, h, fill="#000000", outline="")
        rng = random.Random(state["frame"] + 9)
        specks = max(280, (w * h) // 7000)
        for _ in range(specks):
            x = rng.randint(0, w)
            y = rng.randint(0, h)
            g = rng.randint(0, 255)
            canvas.create_rectangle(
                x,
                y,
                x + rng.randint(1, max(3, w // 400)),
                y + rng.randint(1, max(2, h // 300)),
                fill=f"#{g:02x}{g:02x}{g:02x}",
                outline="",
            )
        if int(t * 12) % 2 == 0:
            canvas.create_text(
                w / 2,
                h / 2,
                text="NO SIGNAL",
                fill="#ffffff",
                font=_mono(h, 0.055, bold=True, minimum=28),
            )
        _preview_hint(canvas, w, h, preview)
        return

    if animation == "nuke":
        remaining = max(0, 3 - int(t * 3.2))
        if t < 0.78:
            canvas.configure(bg="#090909")
            canvas.create_rectangle(0, 0, w, h, fill="#090909", outline="")
            canvas.create_text(
                w / 2,
                h / 2,
                text=str(max(1, remaining)),
                fill="#ffb000",
                font=_mono(h, 0.18, bold=True, minimum=72),
            )
            canvas.create_text(
                w / 2,
                h / 2 + max(70, h // 10),
                text="ARMING",
                fill="#886600",
                font=_mono(h, 0.028, minimum=16),
            )
        else:
            flash = int(255 * min(1.0, (t - 0.78) * 4))
            hex_color = f"#{flash:02x}{flash:02x}{flash:02x}"
            canvas.configure(bg=hex_color)
            canvas.create_rectangle(0, 0, w, h, fill=hex_color, outline="")
            canvas.create_text(
                w / 2,
                h / 2,
                text="SEALED",
                fill="#220000",
                font=_mono(h, 0.09, bold=True, minimum=40),
            )
        _preview_hint(canvas, w, h, preview)
        return

    canvas.configure(bg="#000000")
    canvas.create_rectangle(0, 0, w, h, fill="#000000", outline="")
    message = "LOCKDOWN PROTOCOL INITIATED"
    count = max(1, int(len(message) * min(1.0, t * 1.3)))
    typed = message[:count]
    canvas.create_text(
        max(48, w // 20),
        h / 2,
        text=typed,
        fill="#7CFF7C",
        font=_mono(h, 0.036, minimum=18),
        anchor="w",
    )
    canvas.create_text(
        max(48, w // 20),
        h / 2 + max(32, h // 24),
        text="> sealing configured targets…",
        fill="#3a7a3a",
        font=_mono(h, 0.022, minimum=13),
        anchor="w",
    )
    _preview_hint(canvas, w, h, preview)


def _preview_hint(canvas, w: int, h: int, preview: bool) -> None:
    if not preview:
        return
    canvas.create_text(
        w / 2,
        h - max(16, h // 40),
        text="Enter or Esc to exit preview",
        fill="#666666",
        font=_mono(h, 0.016, minimum=10),
    )


def _banner(canvas, w: int, h: int, text: str, color: str) -> None:
    canvas.create_text(
        w / 2,
        max(36, h // 22),
        text=text,
        fill=color,
        font=_mono(h, 0.032, bold=True, minimum=16),
    )


def _matrix_columns(width: int, height: int) -> list[dict]:
    glyphs = list("01アイウエオカキクケコXENONΞΔ")
    columns = []
    gap = max(16, width // 88)
    x = gap // 2
    while x < width:
        columns.append(
            {
                "x": x,
                "y": random.randint(-height, 0),
                "speed": random.randint(max(8, height // 80), max(18, height // 40)),
                "glyphs": [random.choice(glyphs) for _ in range(max(10, height // 40))],
            }
        )
        x += gap
    return columns


def _script_lines() -> list[str]:
    root = Path(__file__).resolve().parent
    lines: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent)
        lines.append(f">>> exec {rel}")
        try:
            body = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        lines.extend(body[:350])
    return lines or ["# xenon"]


def _play_ansi(
    animation: str,
    duration: float,
    *,
    flash: bool,
    script_flash: bool = False,
    console: bool = False,
    preview: bool = False,
) -> None:
    hide = "\033[?25l\033[2J\033[H"
    show = "\033[?25h\033[0m"
    sys.stdout.write(hide)
    sys.stdout.flush()
    started = time.monotonic()
    frame = 0
    scripts = _script_lines() if script_flash else []
    cursor = 0
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= duration:
                break
            if preview and _skip_key_pressed():
                break
            size = shutil.get_terminal_size((80, 24))
            t = elapsed / max(duration, 0.01)
            sys.stdout.write("\033[H")
            if flash and elapsed < 0.18:
                sys.stdout.write("\033[47;30m")
                sys.stdout.write((" " * size.columns + "\n") * size.lines)
                sys.stdout.write("\033[0m")
            elif script_flash or console:
                sys.stdout.write(
                    _ansi_console_frame(
                        animation,
                        t,
                        size.columns,
                        size.lines,
                        frame,
                        scripts=scripts,
                        cursor=cursor,
                        script_flash=script_flash,
                        preview=preview,
                    )
                )
                cursor += max(1, size.lines - 3)
            else:
                sys.stdout.write(
                    _ansi_frame(
                        animation,
                        t,
                        size.columns,
                        size.lines,
                        frame,
                        preview=preview,
                    )
                )
            sys.stdout.flush()
            frame += 1
            time.sleep(0.03 if script_flash or console else 0.04)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[2J\033[H" + show)
        sys.stdout.flush()


def _ansi_console_frame(
    animation: str,
    t: float,
    cols: int,
    rows: int,
    frame: int,
    *,
    scripts: list[str],
    cursor: int,
    script_flash: bool,
    preview: bool = False,
) -> str:
    cols = max(20, cols)
    rows = max(8, rows)
    invert = script_flash and (frame % 8) < 2
    prefix = "\033[7;32m" if invert else "\033[32m"
    header = "tty3  linux  xenon-panic  (visual console — session stays here)"
    lines = [f"\033[90m{header[:cols].ljust(cols)}\033[0m"]
    body_rows = rows - (2 if preview else 1)
    if script_flash and scripts:
        for index in range(max(1, body_rows)):
            line = scripts[(cursor + index) % len(scripts)]
            lines.append(prefix + line[:cols].ljust(cols) + "\033[0m")
    else:
        dmesg = _console_dmesg(animation)
        shown = min(body_rows - 1, max(4, int(len(dmesg) * min(1.0, t * 1.8))))
        for line in dmesg[:shown]:
            lines.append(prefix + line[:cols].ljust(cols) + "\033[0m")
        while len(lines) < rows - (2 if preview else 1):
            lines.append(prefix + (" " * cols) + "\033[0m")
        blink = "_" if int(t * 8) % 2 == 0 else " "
        lines.append(prefix + f"xenon-panic login:{blink}".ljust(cols)[:cols] + "\033[0m")
    if preview:
        lines.append("\033[90m" + "Enter or Esc to exit preview".center(cols)[:cols] + "\033[0m")
    while len(lines) < rows:
        lines.append(prefix + (" " * cols) + "\033[0m")
    return "\n".join(lines[:rows]) + "\033[0m\n"


def _ansi_frame(
    animation: str,
    t: float,
    cols: int,
    rows: int,
    frame: int,
    preview: bool = False,
) -> str:
    cols = max(20, cols)
    rows = max(8, rows)
    hint = "Enter or Esc to exit preview" if preview else ""
    body_rows = rows - (1 if preview else 0)
    if animation == "matrix":
        rng = random.Random(frame)
        lines = [
            f"\033[32m{''.join(rng.choice('01ΞΔXEN ') for _ in range(cols))}\033[0m"
            for _ in range(body_rows - 1)
        ]
        lines.append(f"\033[92m{'SEALING'.center(cols)}\033[0m")
        if preview:
            lines.append(f"\033[90m{hint.center(cols)}\033[0m")
        return "\n".join(lines[:rows]) + "\n"

    if animation == "glitch":
        rng = random.Random(frame * 3)
        line = "".join(rng.choice("█▓▒░XYN# ") for _ in range(cols))
        body = [
            f"\033[{rng.choice(['35', '36', '37'])}m{line}\033[0m"
            for _ in range(body_rows - 1)
        ]
        lines = [f"\033[95m{'XENON'.center(cols)}\033[0m", *body]
        if preview:
            lines.append(f"\033[90m{hint.center(cols)}\033[0m")
        return "\n".join(lines[:rows]) + "\n"

    if animation == "bsod":
        fill = "\033[44;37m"
        text = [
            ":-(",
            "Your PC ran into a problem that it doesn't want you to see.",
            "Xenon is sealing encryption targets.",
            f"Stop code: XENON_PANIC_{int(t * 99):02d}",
        ]
        lines = [fill + " " * cols for _ in range(rows)]
        for index, row in enumerate(text):
            padded = ("  " + row).ljust(cols)[:cols]
            if 4 + index < rows:
                lines[4 + index] = fill + padded
        if preview and rows:
            lines[-1] = fill + hint.center(cols)[:cols]
        return "\n".join(lines) + "\033[0m\n"

    if animation == "redalert":
        on = int(t * 16) % 2 == 0
        color = "\033[41;97m" if on else "\033[31;40m"
        lines = [color + "XENON PANIC".center(cols) + "\033[0m" for _ in range(body_rows)]
        if preview:
            lines.append(f"\033[90m{hint.center(cols)}\033[0m")
        return "\n".join(lines[:rows]) + "\n"

    if animation == "static":
        rng = random.Random(frame)
        chars = " .:-=+*#%@"
        lines = ["".join(rng.choice(chars) for _ in range(cols)) for _ in range(body_rows)]
        if preview:
            lines.append(hint.center(cols))
        return "\n".join(lines[:rows]) + "\n"

    if animation == "nuke":
        remaining = max(1, 3 - int(t * 3.2))
        if t < 0.78:
            fill = f"\033[33m{str(remaining).center(cols)}\033[0m"
        else:
            fill = f"\033[30;47m{'SEALED'.center(cols)}\033[0m"
        lines = [fill for _ in range(body_rows)]
        if preview:
            lines.append(f"\033[90m{hint.center(cols)}\033[0m")
        return "\n".join(lines[:rows]) + "\n"

    message = "LOCKDOWN PROTOCOL INITIATED"
    typed = message[: max(1, int(len(message) * min(1.0, t * 1.3)))]
    lines = [f"\033[32m> {typed}\033[0m"]
    lines.append("\033[2m> sealing configured targets…\033[0m")
    lines.extend([""] * max(0, body_rows - 2))
    if preview:
        lines.append(f"\033[90m{hint.center(cols)}\033[0m")
    while len(lines) < rows:
        lines.append("")
    return "\n".join(lines[:rows]) + "\n"


def _effect_notify(animation: str) -> None:
    title = ANIMATION_BY_KEY.get(animation, ANIMATION_BY_KEY["none"]).title
    if shutil.which("notify-send"):
        subprocess.Popen(
            ["notify-send", "--urgency=critical", "Xenon Panic", f"{title} — sealing targets"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _effect_sound() -> list[Callable[[], None]]:
    for _ in range(2):
        sys.stdout.write("\a")
    sys.stdout.flush()
    player = next(
        (name for name in ("pw-play", "paplay", "aplay", "ffplay") if shutil.which(name)),
        None,
    )
    if player is None:
        return []
    path = _alarm_wav()
    argv = [player, str(path)]
    if player == "ffplay":
        argv = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    def stop() -> None:
        if proc.poll() is None:
            proc.terminate()
        try:
            path.unlink()
        except OSError:
            pass

    return [stop]


def _alarm_wav() -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="xenon-alarm-", suffix=".wav", delete=False)
    handle.close()
    path = Path(handle.name)
    rate = 22050
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for index in range(int(rate * 1.2)):
            freq = 620 if (index // 1800) % 2 == 0 else 940
            sample = int(12000 * math.sin(2 * math.pi * freq * (index / rate)))
            frames.extend(struct.pack("<h", sample))
        wav.writeframes(bytes(frames))
    return path


def _effect_shake() -> list[Callable[[], None]]:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not shutil.which("hyprctl"):
        return []
    subprocess.run(
        ["hyprctl", "keyword", "cursor:zoom_factor", "1.18"],
        check=False,
        capture_output=True,
    )

    def restore() -> None:
        subprocess.run(
            ["hyprctl", "keyword", "cursor:zoom_factor", "1"],
            check=False,
            capture_output=True,
        )

    return [restore]


def _effect_flash_hypr() -> None:
    if shutil.which("hyprctl"):
        subprocess.run(
            ["hyprctl", "notify", "0", "400", "rgb(ffffff)", "XENON PANIC"],
            check=False,
            capture_output=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    animation = DEFAULT_ANIMATION
    duration = DEFAULT_DURATION
    flash = False
    ansi = False
    script_flash = False
    console = False
    preview = False
    geometry: tuple[int, int] | None = None
    while args:
        flag = args.pop(0)
        if flag == "--animation":
            animation = normalize_animation(args.pop(0) if args else "none")
        elif flag == "--duration":
            duration = normalize_duration(args.pop(0) if args else DEFAULT_DURATION)
        elif flag == "--geometry":
            geometry = parse_geometry(args.pop(0) if args else "")
        elif flag == "--flash":
            flash = True
        elif flag == "--ansi":
            ansi = True
        elif flag == "--script-flash":
            script_flash = True
        elif flag == "--console":
            console = True
        elif flag == "--preview":
            preview = True
        elif flag in {"-h", "--help"}:
            print(
                "Usage: python -m xenon.fx --animation matrix "
                "[--duration 2.5] [--geometry WxH] [--ansi] "
                "[--script-flash] [--console] [--preview]"
            )
            return 0
    want_visual = animation != "none" or script_flash or console
    screen = _screen_size(geometry)
    if ansi or (
        not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY")
    ):
        if want_visual:
            _play_ansi(
                animation,
                duration,
                flash=flash,
                script_flash=script_flash,
                console=console,
                preview=preview,
            )
        return 0
    tk = _load_tk()
    if tk is not None and want_visual:
        _play_tk(
            tk,
            animation,
            duration,
            flash=flash,
            script_flash=script_flash,
            console=console,
            preview=preview,
            screen=screen,
        )
        return 0
    if want_visual:
        _play_ansi(
            animation,
            duration,
            flash=flash,
            script_flash=script_flash,
            console=console,
            preview=preview,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
