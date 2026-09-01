import json
from pathlib import Path

import pytest

from xenon.config import merge_config, save_config
from xenon.panic import run_panic
from xenon.setup_ui import (
    SetupSession,
    _run_check_targets,
    _run_lock_targets,
    _run_unlock_targets,
    _select_targets,
    _split_target_command,
    action_set_password,
    run_setup,
)
from xenon.vault import is_locked, lock_directory, password_opens


def test_split_target_command():
    assert _split_target_command("l") == ("l", None)
    assert _split_target_command("lock 2") == ("lock", 2)
    assert _split_target_command("u 1") == ("u", 1)
    assert _split_target_command("c") == ("c", None)
    assert _split_target_command("! 3") == ("!", 3)
    assert _split_target_command("panic") == ("panic", None)
    assert _split_target_command("d 1") == ("d", 1)
    assert _split_target_command("p 2") == ("p", 2)
    assert _split_target_command("a") is None
    assert _split_target_command("l x") is None
    assert _split_target_command("lock 1 extra") is None


def test_select_targets():
    targets = ["/tmp/a", "/tmp/b"]
    assert _select_targets(targets, None) == targets
    assert _select_targets(targets, 1) == ["/tmp/a"]
    assert _select_targets(targets, 2) == ["/tmp/b"]
    with pytest.raises(ValueError, match="Invalid"):
        _select_targets(targets, 3)
    with pytest.raises(ValueError, match="No targets"):
        _select_targets([], None)


def test_setup_lock_unlock_and_check(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    config = merge_config({"targets": [str(sample)]})
    session = SetupSession(
        config=config,
        config_file=tmp_path / "config.json",
        password="unit-test-password",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)

    _run_check_targets(session, [str(sample)])
    _run_lock_targets(session, [str(sample)])
    assert is_locked(sample)
    _run_check_targets(session, [str(sample)])
    _run_unlock_targets(session, [str(sample)])
    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "hello"


def test_panic_skips_already_locked(tmp_path: Path):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "unit-test-password")
    assert is_locked(sample)

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
        },
        config_path,
    )

    result = run_panic("unit-test-password", config_file=config_path)
    assert result == [sample.resolve()]
    assert is_locked(sample)
    payload = json.loads((sample / ".xenon" / "manifest.json").read_text())
    assert payload["cipher"]["algorithm"]


def test_setup_rejects_wrong_password_on_locked_vault(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "correct-horse")
    session = SetupSession(
        config=merge_config({"targets": [str(sample)]}),
        config_file=tmp_path / "config.json",
        password="attacker-password",
        password_verified=False,
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: "attacker-password",
    )

    action_set_password(session)
    assert session.password is None
    assert session.password_verified is False
    assert session.message_ok is False

    _run_unlock_targets(session, [str(sample)])
    assert is_locked(sample)
    ciphertext = [
        path
        for path in sample.rglob("*")
        if path.is_file() and ".xenon" not in path.parts
    ]
    assert ciphertext
    assert all(path.read_bytes().startswith(b"XENON") for path in ciphertext)


def test_setup_verifies_existing_vault_password(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "correct-horse")
    session = SetupSession(
        config=merge_config({"targets": [str(sample)]}),
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: "correct-horse",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)

    action_set_password(session)
    assert session.password == "correct-horse"
    assert session.password_verified is True

    _run_unlock_targets(session, [str(sample)])
    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "hello"


def test_setup_entry_rejects_wrong_password_when_locked(
    tmp_path: Path,
    monkeypatch,
):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "correct-horse")
    config_path = tmp_path / "config.json"
    save_config({"targets": [str(sample)]}, config_path)
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: "attacker-password",
    )
    assert run_setup(config_file=config_path) == 1
    assert is_locked(sample)
