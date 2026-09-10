from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


MARKER_START = "# xenon-panic-start"
MARKER_END = "# xenon-panic-end"


def upsert_marked_section(
    path: Path,
    body: str,
    *,
    start: str = MARKER_START,
    end: str = MARKER_END,
) -> None:
    block = f"{start}\n{body.rstrip()}\n{end}\n"
    path = path.expanduser()
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(start) + r".*?" + re.escape(end) + r"\n?",
            re.S,
        )
        if pattern.search(text):
            text = pattern.sub(block, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += "\n" + block
        path.write_text(text, encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block, encoding="utf-8")


def ensure_line(path: Path, line: str, *, comment: str | None = None) -> None:
    path = path.expanduser()
    needle = line.strip()
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if needle in text:
            return
        if text and not text.endswith("\n"):
            text += "\n"
        extra = ""
        if comment:
            extra = f"{comment}\n"
        path.write_text(text + f"\n{extra}{line}\n", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [comment, line] if comment else [line]
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def run_quiet(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    binary = argv[0]
    if os.path.sep not in binary and not shutil.which(binary):
        return None
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
    )


def snippet_path() -> Path:
    return Path.home() / ".local" / "lib" / "xenon" / "panic-bind.txt"


def write_snippet(text: str) -> Path:
    path = snippet_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path
