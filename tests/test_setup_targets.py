import json
from pathlib import Path

import pytest

from xenon.config import merge_config, password_configured, save_config, set_config_password
from xenon.panic import run_panic
from xenon.setup_ui import (
    SetupSession,
    action_encryption_type,
    action_set_password,
    _run_check_targets,
    _run_lock_targets,
    _run_panic_targets,
    _run_unlock_targets,
    _select_targets,
    _split_target_command,
    run_setup,
)
from xenon.vault import (
    is_destroyed_vault,
    is_locked,
    is_passwordless_vault,
    lock_directory,
    password_opens,
)


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
    set_config_password(config, "unit-test-password")
    session = SetupSession(
        config=config,
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: "unit-test-password",
    )

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
    config = {
        "targets": [str(sample)],
        "lockdown": {
            "enabled": False,
            "backend": "none",
            "lock_screen": False,
            "kill_session": False,
        },
    }
    set_config_password(config, "unit-test-password")
    save_config(config, config_path)

    result = run_panic("unit-test-password", config_file=config_path)
    assert result == [sample.resolve()]
    assert is_locked(sample)
    payload = json.loads((sample / ".xenon" / "manifest.json").read_text())
    assert payload["cipher"]["algorithm"]


def test_panic_trigger_locks_without_password(tmp_path: Path):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
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
    result = run_panic(triggered=True, config_file=config_path)
    assert result == [sample]
    assert is_locked(sample)
    assert is_passwordless_vault(sample)


def test_panic_trigger_skips_already_locked(tmp_path: Path):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "unit-test-password")
    config_path = tmp_path / "config.json"
    config = {
        "targets": [str(sample)],
        "lockdown": {
            "enabled": False,
            "backend": "none",
            "lock_screen": False,
            "kill_session": False,
        },
    }
    set_config_password(config, "unit-test-password")
    save_config(config, config_path)
    result = run_panic(triggered=True, config_file=config_path)
    assert result == [sample]
    assert is_locked(sample)
    assert not is_passwordless_vault(sample)


def test_panic_refuses_without_configured_password(tmp_path: Path):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
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
    with pytest.raises(ValueError, match="configured password"):
        run_panic("unit-test-password", config_file=config_path)
    with pytest.raises(ValueError, match="cannot run without"):
        run_panic("", config_file=config_path)
    assert not is_locked(sample)


def test_panic_rejects_wrong_password(tmp_path: Path):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config = {
        "targets": [str(sample)],
        "lockdown": {
            "enabled": False,
            "backend": "none",
            "lock_screen": False,
            "kill_session": False,
        },
    }
    set_config_password(config, "correct-horse")
    save_config(config, config_path)
    with pytest.raises(ValueError, match="Wrong password"):
        run_panic("wrong-battery", config_file=config_path)
    assert not is_locked(sample)


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
    assert password_configured(session.config)

    _run_unlock_targets(session, [str(sample)])
    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "hello"


def test_setup_opens_when_targets_are_locked(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "correct-horse")
    config_path = tmp_path / "config.json"
    save_config({"targets": [str(sample)]}, config_path)
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("setup must not prompt for a password on entry")
        ),
    )
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "8")
    assert run_setup(config_file=config_path) == 0
    assert is_locked(sample)


def test_setup_lock_unlock_passwordless(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    session = SetupSession(
        config=merge_config(
            {
                "targets": [str(sample)],
                "encryption": {"aggressiveness": 2},
            }
        ),
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("passwordless lock/unlock must not prompt")
        ),
    )

    _run_lock_targets(session, [str(sample)])
    assert is_locked(sample)
    assert is_passwordless_vault(sample)
    assert not password_configured(session.config)

    _run_unlock_targets(session, [str(sample)])
    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "hello"


def test_setup_panic_runs_without_password(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    session = SetupSession(
        config=merge_config(
            {
                "targets": [str(sample)],
                "lockdown": {
                    "enabled": False,
                    "backend": "none",
                    "lock_screen": False,
                    "kill_session": False,
                },
            }
        ),
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "YES")

    _run_panic_targets(session, [str(sample)])
    assert is_locked(sample)
    assert is_passwordless_vault(sample)


def test_setup_change_password_requires_old(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    session = SetupSession(
        config=merge_config({"targets": [str(sample)]}),
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    prompts: list[str] = []

    def fake_getpass(*args, **kwargs):
        assert prompts, "unexpected password prompt"
        return prompts.pop(0)

    monkeypatch.setattr("xenon.setup_ui.getpass.getpass", fake_getpass)

    prompts.extend(["first-secret", "first-secret"])
    action_set_password(session)
    assert session.password == "first-secret"
    assert password_configured(session.config)

    prompts.extend(["first-secret"])
    _run_lock_targets(session, [str(sample)])
    assert is_locked(sample)
    assert password_opens(sample, "first-secret")

    prompts.extend(["wrong-secret"])
    action_set_password(session)
    assert session.password is None
    assert session.message_ok is False
    assert password_opens(sample, "first-secret")

    prompts.extend(["first-secret", "second-secret", "second-secret"])
    action_set_password(session)
    assert session.password == "second-secret"
    assert password_opens(sample, "second-secret")
    assert not password_opens(sample, "first-secret")


def test_setup_lock_unlock_always_prompts_when_password_set(
    tmp_path: Path,
    monkeypatch,
):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    config = merge_config({"targets": [str(sample)]})
    set_config_password(config, "correct-horse")
    session = SetupSession(
        config=config,
        config_file=tmp_path / "config.json",
        password="correct-horse",
        password_verified=True,
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    prompts: list[str] = []

    def fake_getpass(*args, **kwargs):
        assert prompts, "lock/unlock must ask for the password"
        return prompts.pop(0)

    monkeypatch.setattr("xenon.setup_ui.getpass.getpass", fake_getpass)

    prompts.extend(["wrong-battery"])
    _run_lock_targets(session, [str(sample)])
    assert not is_locked(sample)
    assert session.password is None

    prompts.extend(["correct-horse"])
    _run_lock_targets(session, [str(sample)])
    assert is_locked(sample)

    prompts.extend(["wrong-battery"])
    _run_unlock_targets(session, [str(sample)])
    assert is_locked(sample)

    prompts.extend(["correct-horse"])
    _run_unlock_targets(session, [str(sample)])
    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "hello"


def test_setup_level_5_requires_password_and_destroy(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    session = SetupSession(
        config=merge_config({"targets": [str(sample)]}),
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "5")
    action_encryption_type(session)
    assert session.message_ok is False
    assert session.config["encryption"].get("aggressiveness", 3) == 3

    set_config_password(session.config, "correct-horse")
    answers = iter(["5", "DESTROY", ""])
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(
        "xenon.setup_ui.getpass.getpass",
        lambda *args, **kwargs: "correct-horse",
    )
    action_encryption_type(session)
    assert session.message_ok is True
    assert session.config["encryption"]["aggressiveness"] == 5


def test_setup_delete_level_requires_yes(tmp_path: Path, monkeypatch):
    sample = tmp_path / "docs"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    session = SetupSession(
        config=merge_config(
            {
                "targets": [str(sample)],
                "encryption": {"aggressiveness": 4},
            }
        ),
        config_file=tmp_path / "config.json",
    )
    monkeypatch.setattr("xenon.setup_ui._pause", lambda *args, **kwargs: None)
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "nope")
    _run_lock_targets(session, [str(sample)])
    assert (sample / "a.txt").is_file()
    assert not is_destroyed_vault(sample)

    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: "YES")
    _run_lock_targets(session, [str(sample)])
    assert not sample.exists()
    assert not is_destroyed_vault(sample)
