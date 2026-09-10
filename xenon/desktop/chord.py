from __future__ import annotations

from dataclasses import dataclass


MOD_CANON = {
    "super": "Super",
    "mod4": "Super",
    "meta": "Super",
    "win": "Super",
    "logo": "Super",
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "primary": "Ctrl",
    "alt": "Alt",
    "mod1": "Alt",
    "shift": "Shift",
}

MOD_ORDER = ("Super", "Ctrl", "Alt", "Shift")


@dataclass(frozen=True)
class Chord:
    mods: tuple[str, ...]
    key: str

    def display(self) -> str:
        parts = list(self.mods) + [self.key]
        return "+".join(parts)


def parse_chord(text: str | None) -> Chord:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Hotkey chord cannot be empty.")

    if "," in raw:
        mods_part, key = raw.rsplit(",", 1)
        tokens = mods_part.replace("+", " ").replace("-", " ").split()
        key = key.strip()
    else:
        tokens = [
            part.strip()
            for part in raw.replace("-", "+").split("+")
            if part.strip()
        ]
        if len(tokens) < 2:
            raise ValueError(
                "Chord needs modifiers and a key, e.g. Super+Ctrl+Alt+Shift+X"
            )
        key = tokens[-1]
        tokens = tokens[:-1]

    if not key:
        raise ValueError("Chord is missing the key.")

    seen: set[str] = set()
    for token in tokens:
        canon = MOD_CANON.get(token.lower())
        if canon is None:
            raise ValueError(f"Unknown modifier: {token}")
        seen.add(canon)

    ordered = tuple(mod for mod in MOD_ORDER if mod in seen)
    if not ordered:
        raise ValueError(
            "Chord needs at least one modifier (Super, Ctrl, Alt, or Shift)."
        )
    return Chord(mods=ordered, key=key)


def hyprland_bind(chord: Chord) -> str:
    mods = " ".join(mod.upper() for mod in chord.mods) or "SUPER"
    return f"{mods}, {chord.key}"


def sway_bindsym(chord: Chord) -> str:
    mapping = {"Super": "Mod4", "Ctrl": "Ctrl", "Alt": "Alt", "Shift": "Shift"}
    parts = [mapping[mod] for mod in chord.mods]
    parts.append(chord.key)
    return "+".join(parts)


def gtk_accel(chord: Chord, *, control: str = "Primary") -> str:
    mapping = {
        "Super": "Super",
        "Ctrl": control,
        "Alt": "Alt",
        "Shift": "Shift",
    }
    key = chord.key.lower() if len(chord.key) == 1 else chord.key
    return "".join(f"<{mapping[mod]}>" for mod in chord.mods) + key


def gnome_accel(chord: Chord) -> str:
    return gtk_accel(chord, control="Control")


def kde_shortcut(chord: Chord) -> str:
    mapping = {"Super": "Meta", "Ctrl": "Ctrl", "Alt": "Alt", "Shift": "Shift"}
    parts = [mapping[mod] for mod in chord.mods]
    parts.append(chord.key)
    return "+".join(parts)


def niri_bind(chord: Chord) -> str:
    mapping = {"Super": "Mod", "Ctrl": "Ctrl", "Alt": "Alt", "Shift": "Shift"}
    parts = [mapping[mod] for mod in chord.mods]
    parts.append(_letter_key(chord.key, upper=True))
    return "+".join(parts)


def river_map(chord: Chord) -> tuple[str, str]:
    mapping = {
        "Super": "Super",
        "Ctrl": "Control",
        "Alt": "Alt",
        "Shift": "Shift",
    }
    mods = "+".join(mapping[mod] for mod in chord.mods)
    return mods, chord.key


def _letter_key(key: str, *, upper: bool) -> str:
    if len(key) != 1 or not key.isalpha():
        return key
    return key.upper() if upper else key.lower()
