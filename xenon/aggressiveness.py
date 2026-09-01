from __future__ import annotations

from dataclasses import dataclass


DEFAULT_AGGRESSIVENESS = 3


@dataclass(frozen=True)
class AggressivenessLevel:
    level: int
    key: str
    title: str
    summary: str
    reversible: bool
    needs_password: bool
    passwordless: bool
    destructive: bool


LEVELS: tuple[AggressivenessLevel, ...] = (
    AggressivenessLevel(
        level=1,
        key="read-protect",
        title="Read protection",
        summary="No encryption. Password gates read access via file permissions.",
        reversible=True,
        needs_password=True,
        passwordless=False,
        destructive=False,
    ),
    AggressivenessLevel(
        level=2,
        key="passwordless",
        title="Passwordless encryption",
        summary="Encrypt in place. No password needed to unlock.",
        reversible=True,
        needs_password=False,
        passwordless=True,
        destructive=False,
    ),
    AggressivenessLevel(
        level=3,
        key="standard",
        title="Standard encryption",
        summary="Encrypt in place. Password required to lock and unlock.",
        reversible=True,
        needs_password=True,
        passwordless=False,
        destructive=False,
    ),
    AggressivenessLevel(
        level=4,
        key="delete",
        title="Delete without overwrite",
        summary="Remove files without overwriting. Data may still be recoverable.",
        reversible=False,
        needs_password=False,
        passwordless=False,
        destructive=True,
    ),
    AggressivenessLevel(
        level=5,
        key="destroy",
        title="Secure destruction",
        summary="Multiple overwrites, then delete. Irreversible.",
        reversible=False,
        needs_password=True,
        passwordless=False,
        destructive=True,
    ),
)

LEVEL_BY_NUMBER = {item.level: item for item in LEVELS}


def normalize_aggressiveness(value: object) -> int:
    if value is None or value == "":
        return DEFAULT_AGGRESSIVENESS
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid aggressiveness: {value!r}") from exc
    if level not in LEVEL_BY_NUMBER:
        raise ValueError("Aggressiveness must be a level from 1 to 5.")
    return level


def get_level(value: object) -> AggressivenessLevel:
    return LEVEL_BY_NUMBER[normalize_aggressiveness(value)]


def describe_level(value: object) -> str:
    spec = get_level(value)
    return f"{spec.level} · {spec.title}"
