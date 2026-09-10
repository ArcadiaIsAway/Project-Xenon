from xenon.config import merge_config
from xenon.fx import (
    SpectacleRun,
    apply_preset,
    describe_fx,
    fx_from_config,
    normalize_animation,
    normalize_effects,
    playback_from_config,
    play_panic_spectacle,
    preview_panic_spectacle,
)
from xenon.panic import run_panic
from xenon.vault import is_locked


def test_play_skips_under_pytest():
    play_panic_spectacle({"panic": {"animation": "matrix", "duration": 8, "effects": ["sound"]}})
    assert normalize_animation(None) == "none"
    assert normalize_animation("MATRIX") == "matrix"
    assert normalize_animation("kernel") == "bsod"
    assert normalize_animation("nope") == "none"
    assert normalize_effects(["Sound", "flash", "sound"]) == ["sound", "flash"]
    assert normalize_effects("notify, shake") == ["notify", "shake"]
    assert normalize_effects(["nope"]) == []


def test_merge_config_panic_defaults():
    config = merge_config({"targets": ["/tmp"]})
    animation, effects, duration = fx_from_config(config)
    assert animation == "none"
    assert effects == []
    assert duration == 2.5
    playback = playback_from_config(config)
    assert playback.preset == "none"
    assert playback.display == "overlay"
    assert playback.music == ""
    assert playback.poweroff_after_track is False
    config = merge_config(
        {
            "targets": ["/tmp"],
            "panic": {"animation": "glitch", "effects": ["sound"], "duration": 99},
        }
    )
    assert fx_from_config(config) == ("glitch", ["sound"], 30.0)
    assert "glitch" in describe_fx(config).lower() or "Glitch" in describe_fx(config)


def test_presets_and_custom_music():
    rave = apply_preset("rave", music="/tmp/theme.ogg")
    assert rave.animation == "glitch"
    assert "flash" in rave.effects
    assert rave.music == "/tmp/theme.ogg"
    storm = apply_preset("tty-storm")
    assert storm.display == "tty"
    assert storm.script_flash is True
    finale = apply_preset("finale")
    assert finale.poweroff_after_track is True
    assert finale.wait_for_music is True


def test_run_panic_invokes_spectacle(tmp_path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    from xenon.config import save_config

    config_path = tmp_path / "config.json"
    save_config(
        {
            "targets": [str(sample)],
            "lockdown": {
                "enabled": False,
                "backend": "none",
                "lock_screen": False,
                "kill_session": False,
            },
            "panic": {"animation": "matrix", "effects": ["notify"]},
        },
        config_path,
    )
    seen: list[str] = []

    def fake_play(config, *, preview=False):
        seen.append(config["panic"]["animation"])
        return None

    monkeypatch.setattr("xenon.panic.play_panic_spectacle", fake_play)
    run_panic(triggered=True, config_file=config_path)
    assert seen == ["matrix"]
    assert is_locked(sample)


def test_preview_never_locks_or_powers_off(tmp_path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    powered: list[bool] = []
    started: list[str] = []

    def fake_start(playback, run):
        started.append(playback.animation)

    monkeypatch.setattr("xenon.fx._start_spectacle", fake_start)
    monkeypatch.setattr("xenon.fx._poweroff", lambda: powered.append(True))

    run = preview_panic_spectacle(
        {
            "targets": [str(sample)],
            "panic": {
                "animation": "nuke",
                "effects": ["sound"],
                "poweroff_after_track": True,
                "wait_for_music": True,
            },
        }
    )
    assert started == ["nuke"]
    assert run.preview is True
    assert run.poweroff_after_track is False
    assert powered == []
    assert not is_locked(sample)


def test_spectacle_finish_preview_skips_poweroff(monkeypatch):
    powered: list[bool] = []
    monkeypatch.setattr("xenon.fx._poweroff", lambda: powered.append(True))
    run = SpectacleRun(preview=True, poweroff_after_track=True, duration=0.01)
    run.finish()
    assert powered == []


def test_cli_preview_does_not_run_panic(monkeypatch):
    from xenon.cli import main

    seen: list[str] = []
    monkeypatch.setattr(
        "xenon.cli.preview_panic_spectacle",
        lambda _config: seen.append("preview"),
    )
    monkeypatch.setattr(
        "xenon.cli.run_panic",
        lambda *a, **k: seen.append("panic"),
    )
    monkeypatch.setattr(
        "xenon.cli.confirm",
        lambda **_k: seen.append("confirm") or True,
    )
    assert main(["panic", "--preview"]) == 0
    assert seen == ["preview"]
    seen.clear()
    assert main(["panic", "--preview", "--trigger"]) == 0
    assert seen == ["preview"]


def test_screen_size_and_geometry(monkeypatch):
    import xenon.fx as fx

    assert fx.parse_geometry("2560x1440") == (2560, 1440)
    assert fx.parse_geometry("nope") is None
    monkeypatch.setattr(fx.shutil, "which", lambda name: "/usr/bin/hyprctl" if name == "hyprctl" else None)

    class Result:
        stdout = '[{"focused": false, "width": 1920, "height": 1080}, {"focused": true, "width": 2560, "height": 1440}]'

    monkeypatch.setattr(fx.subprocess, "run", lambda *a, **k: Result())
    assert fx._screen_size() == (2560, 1440)
    columns = fx._matrix_columns(2560, 1440)
    assert columns[0]["x"] < 40
    assert columns[-1]["x"] >= 2560 - 80


def test_preview_enter_stops_overlay(monkeypatch):
    import xenon.fx as fx

    class FakeProc:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def wait(self, timeout=None):
            if self.terminated:
                return 0
            raise fx.subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

        def terminate(self):
            self.terminated = True

    proc = FakeProc()
    run = SpectacleRun(preview=True, duration=8, processes=[proc])
    monkeypatch.setattr(fx, "_skip_key_pressed", lambda: True)
    run.finish()
    assert proc.terminated is True


def test_tty_mode_stays_on_overlay(monkeypatch):
    import xenon.fx as fx

    assert not hasattr(fx, "_spawn_tty")
    spawned: list[tuple[object, bool]] = []
    monkeypatch.setattr(fx, "_screen_size", lambda forced=None: forced or (2560, 1440))

    class FakeProc:
        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

        def poll(self):
            return 0

    def fake_overlay(playback, preview=False):
        spawned.append((playback, preview))
        return FakeProc()

    monkeypatch.setattr(fx, "_spawn_overlay_process", fake_overlay)
    playback = apply_preset("tty-storm")
    assert playback.display == "tty"
    assert playback.script_flash is True
    run = SpectacleRun(preview=True)
    fx._start_visuals(playback, run)
    assert len(spawned) == 1
    assert spawned[0][1] is True
    argv = fx._visual_argv(spawned[0][0], ansi=False, preview=True)
    assert "--console" in argv
    assert "--script-flash" in argv
    assert "--preview" in argv
    assert "--geometry" in argv
    assert "2560x1440" in argv
    assert "openvt" not in argv
    frame = fx._ansi_console_frame(
        "matrix",
        0.5,
        80,
        24,
        0,
        scripts=[],
        cursor=0,
        script_flash=False,
        preview=True,
    )
    assert "visual console" in frame
    assert "tty3" in frame
    assert frame.count("\n") >= 24
    assert "Enter or Esc" in frame
