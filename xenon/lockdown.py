from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from typing import Callable, Sequence


LogFn = Callable[[str], None]


def _log(message: str, log: LogFn | None) -> None:
    if log:
        log(message)
    else:
        print(message)


def _as_command(command: Sequence[str] | str | None) -> list[str] | None:
    if command is None:
        return None
    if isinstance(command, str):
        return shlex.split(command)
    return list(command)


def _run(
    command: Sequence[str],
    *,
    background: bool = False,
    log: LogFn | None = None,
) -> bool:
    rendered = " ".join(command)
    _log(f"Running: {rendered}", log)

    if background:
        subprocess.Popen(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.3)
        return True

    result = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            _log(detail, log)
        return False
    return True


def _expand_command(command: Sequence[str]) -> list[str]:
    session = os.environ.get("XDG_SESSION_ID", "")
    return [
        part.replace("${XDG_SESSION_ID}", session)
        for part in command
    ]


def lock_screen_auto(log: LogFn | None = None) -> bool:
    if shutil.which("loginctl"):
        if _run(["loginctl", "lock-session"], log=log):
            return True

    for binary in ("hyprlock", "swaylock", "waylock", "i3lock", "betterlockscreen"):
        if shutil.which(binary):
            args = [binary]
            if binary == "betterlockscreen":
                args = [binary, "-l"]
            return _run(args, background=True, log=log)

    _log("No screen-lock tool found; skipping.", log)
    return False


def kill_session_auto(log: LogFn | None = None) -> bool:
    session = os.environ.get("XDG_SESSION_ID")

    if session and shutil.which("loginctl"):
        if _run(["loginctl", "terminate-session", session], log=log):
            return True

    if shutil.which("hyprctl"):
        if _run(["hyprctl", "dispatch", "exit"], log=log):
            return True

    if shutil.which("swaymsg"):
        if _run(["swaymsg", "exit"], log=log):
            return True

    _log("No session-kill method available.", log)
    return False


def lockdown(
    *,
    backend: str = "auto",
    lock_screen: bool = True,
    kill_session: bool = True,
    lock_command: Sequence[str] | str | None = None,
    kill_command: Sequence[str] | str | None = None,
    log: LogFn | None = None,
) -> None:
    backend = (backend or "auto").lower()
    lock_command = _as_command(lock_command)
    kill_command = _as_command(kill_command)

    if backend in {"none", "off", "disabled"}:
        _log("Lockdown backend disabled.", log)
        return

    if backend == "commands":
        if lock_screen:
            if not lock_command:
                raise ValueError(
                    "lockdown.backend=commands requires lockdown.lock_command"
                )
            _run(_expand_command(lock_command), background=True, log=log)
        if kill_session:
            if not kill_command:
                raise ValueError(
                    "lockdown.backend=commands requires lockdown.kill_command"
                )
            _run(_expand_command(kill_command), log=log)
        return

    if backend not in {"auto", "logind"}:
        raise ValueError(
            f"Unknown lockdown backend: {backend}. "
            f"Use auto, logind, commands, or none."
        )

    if lock_screen:
        lock_screen_auto(log)

    if kill_session:
        kill_session_auto(log)
