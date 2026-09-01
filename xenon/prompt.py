from __future__ import annotations

import getpass
import shutil
import subprocess


def prompt_password(
    *,
    gui: bool = False,
    title: str = "Xenon",
    prompt: str = "Master password:",
) -> str:
    if gui:
        password = _gui_password(title=title, prompt=prompt)
        if password is not None:
            return password

    return getpass.getpass(f"{prompt} ")


def confirm(
    *,
    gui: bool = False,
    title: str = "Xenon Panic",
    text: str = "Trigger panic lockdown?",
) -> bool:
    if gui:
        answered = _gui_confirm(title=title, text=text)
        if answered is not None:
            return answered

    answer = input(f"{text} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _gui_password(*, title: str, prompt: str) -> str | None:
    if shutil.which("zenity"):
        result = subprocess.run(
            [
                "zenity",
                "--password",
                f"--title={title}",
                f"--text={prompt}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise KeyboardInterrupt
        return result.stdout.rstrip("\n")

    if shutil.which("kdialog"):
        result = subprocess.run(
            [
                "kdialog",
                "--title",
                title,
                "--password",
                prompt,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise KeyboardInterrupt
        return result.stdout.rstrip("\n")

    return None


def _gui_confirm(*, title: str, text: str) -> bool | None:
    if shutil.which("zenity"):
        result = subprocess.run(
            [
                "zenity",
                "--question",
                f"--title={title}",
                f"--text={text}",
                "--ok-label=Panic",
                "--cancel-label=Cancel",
            ],
            check=False,
        )
        return result.returncode == 0

    if shutil.which("kdialog"):
        result = subprocess.run(
            [
                "kdialog",
                "--title",
                title,
                "--yesno",
                text,
            ],
            check=False,
        )
        return result.returncode == 0

    return None
