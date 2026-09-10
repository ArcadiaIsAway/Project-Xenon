from xenon.tui import Choice, match_choice, pick


def test_match_choice_number_shortcut_and_key():
    items = [
        Choice("targets", "Targets", shortcut="1"),
        Choice("panic", "Panic", shortcut="7"),
        Choice("exit", "Exit", shortcut="8"),
    ]
    assert match_choice("1", items) == "targets"
    assert match_choice("2", items) == "panic"
    assert match_choice("7", items) == "panic"
    assert match_choice("panic", items) == "panic"
    assert match_choice("Exit", items) == "exit"
    assert match_choice("", items) is None
    assert match_choice("nope", items) is None


def test_pick_simple_selects_and_backs_out(monkeypatch):
    items = [
        Choice("a", "Alpha", group="Letters", shortcut="1"),
        Choice("b", "Beta", group="Letters", shortcut="2"),
    ]
    monkeypatch.setattr("xenon.tui.interactive", lambda: False)
    monkeypatch.setattr("xenon.tui.clear_screen", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "2")
    assert pick("Test", items) == "b"
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")
    assert pick("Test", items) is None
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "back")
    assert pick("Test", items, allow_back=True) is None
