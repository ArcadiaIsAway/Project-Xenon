import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.sh"


def _require_venv_python() -> Path:
    path = ROOT / ".venv" / "bin" / "python"
    if not path.is_file():
        pytest.skip("project virtualenv is not built")
    return path


def test_install_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(INSTALL)], check=True)


def test_install_script_help():
    result = subprocess.run(
        ["bash", str(INSTALL), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--bin-dir" in result.stdout
    assert "PATH" in result.stdout


def test_install_places_xenon_on_path(tmp_path: Path):
    _require_venv_python()
    bindir = tmp_path / "on-path"
    bindir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '/usr/bin')}"
    env["XENON_SKIP_VENV"] = "1"
    env.pop("XDG_BIN_HOME", None)

    result = subprocess.run(
        ["bash", str(INSTALL)],
        cwd=str(ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    command = bindir / "xenon"
    assert command.is_file(), result.stdout
    assert command.stat().st_mode & stat.S_IXUSR
    text = command.read_text(encoding="utf-8")
    assert "-m xenon" in text
    assert str(ROOT / ".venv" / "bin" / "python") in text
    assert (home / ".profile").is_file()
    profile = (home / ".profile").read_text(encoding="utf-8")
    assert str(bindir) in profile
    assert (home / ".config" / "fish" / "conf.d" / "xenon-path.fish").is_file()


def test_install_creates_local_bin_when_path_has_no_writable_dir(tmp_path: Path):
    _require_venv_python()
    home = tmp_path / "home"
    home.mkdir()
    locked = tmp_path / "locked-bin"
    locked.mkdir()
    locked.chmod(0o555)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{locked}{os.pathsep}/usr/bin{os.pathsep}/bin"
    env["XENON_SKIP_VENV"] = "1"
    env.pop("XDG_BIN_HOME", None)

    try:
        subprocess.run(
            ["bash", str(INSTALL)],
            cwd=str(ROOT),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        command = home / ".local" / "bin" / "xenon"
        assert command.is_file()
        profile = (home / ".profile").read_text(encoding="utf-8")
        assert str(home / ".local" / "bin") in profile
    finally:
        locked.chmod(0o755)
