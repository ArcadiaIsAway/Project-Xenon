"""Interactive setup menus: grouped lists, arrow keys, numbered fallback."""

from __future__ import annotations

import os
import select
import shutil
import sys
from dataclasses import dataclass


BANNER_ART = r"""
/\ \ /\ \ /\  _`\ /\ \/\ \/\  __`\/\ \/\ \    
\ `\`\/'/'\ \ \L\_\ \ `\\ \ \ \/\ \ \ `\\ \   
 `\/ > <   \ \  _\L\ \ , ` \ \ \ \ \ \ , ` \  
    \/'/\`\ \ \ \L\ \ \ \`\ \ \ \_\ \ \ \`\ \ 
    /\_\\ \_\\ \____/\ \_\ \_\ \_____\ \_\ \_\
    \/_/ \/_/ \/___/  \/_/\/_/\/_____/\/_/\/_/
""".strip(
    "\n"
)

_BANNER_ROW = ("38;5;118", "38;5;82", "38;5;46", "38;5;40", "38;5;34", "38;5;22")


class Style:
    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self.wrap("1", text)

    def dim(self, text: str) -> str:
        return self.wrap("2", text)

    def cyan(self, text: str) -> str:
        return self.wrap("96", text)

    def green(self, text: str) -> str:
        return self.wrap("92", text)

    def yellow(self, text: str) -> str:
        return self.wrap("93", text)

    def red(self, text: str) -> str:
        return self.wrap("91", text)

    def magenta(self, text: str) -> str:
        return self.wrap("95", text)

    def invert(self, text: str) -> str:
        return self.wrap("1;30;42", text)


STYLE = Style()


@dataclass(frozen=True)
class Choice:
    key: str
    label: str
    hint: str = ""
    group: str = ""
    current: bool = False
    danger: bool = False
    shortcut: str = ""


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def term_width() -> int:
    return max(60, min(shutil.get_terminal_size((80, 24)).columns, 100))


def rule(char: str = "─") -> str:
    return char * term_width()


def render_banner() -> str:
    lines = BANNER_ART.splitlines()
    width = max(len(line) for line in lines)
    painted: list[str] = []
    for index, line in enumerate(lines):
        color = _BANNER_ROW[min(index, len(_BANNER_ROW) - 1)]
        padded = line.ljust(width)
        if STYLE.enabled:
            painted.append(f"\033[1;{color}m{padded}\033[0m")
        else:
            painted.append(padded)
    if not STYLE.enabled:
        return "\n".join(painted)
    bar = STYLE.dim("─" * (width + 2))
    top = f"{STYLE.dim('┌')}{bar}{STYLE.dim('┐')}"
    bottom = f"{STYLE.dim('└')}{bar}{STYLE.dim('┘')}"
    return "\n".join(
        [
            top,
            *[f"{STYLE.dim('│')} {row} {STYLE.dim('│')}" for row in painted],
            bottom,
        ]
    )


def interactive() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get("XENON_SIMPLE_MENU") == "1":
        return False
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return True


def ask_text(prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(STYLE.cyan(f"{prompt}{suffix} › ")).strip()
    return raw or default


def match_choice(raw: str, choices: list[Choice]) -> str | None:
    text = raw.strip()
    if not text:
        return None
    lowered = text.lower()
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(choices):
            return choices[index - 1].key
    for item in choices:
        if item.shortcut and item.shortcut.lower() == lowered:
            return item.key
    for item in choices:
        if item.key.lower() == lowered:
            return item.key
    for item in choices:
        if item.label.lower() == lowered:
            return item.key
    return None


def pick(
    title: str,
    choices: list[Choice],
    *,
    subtitle: str = "",
    header: str | list[str] | None = None,
    allow_back: bool = True,
    cursor: int = 0,
) -> str | None:
    """Select one item. Arrow keys on a TTY; typed numbers otherwise.

    Returns the choice key, or None if the user backs out.
    """
    if not choices:
        return None
    if not interactive():
        return _pick_simple(title, choices, subtitle=subtitle, header=header, allow_back=allow_back)
    return _pick_interactive(
        title,
        choices,
        subtitle=subtitle,
        header=header,
        allow_back=allow_back,
        cursor=cursor,
    )


def _header_lines(header: str | list[str] | None) -> list[str]:
    if header is None:
        return []
    if isinstance(header, str):
        return [header] if header else []
    return list(header)


def _draw_menu(
    title: str,
    choices: list[Choice],
    *,
    subtitle: str,
    header: str | list[str] | None,
    cursor: int,
    highlight: bool,
    allow_back: bool,
) -> None:
    clear_screen()
    print(render_banner())
    print()
    print(STYLE.bold(title))
    if subtitle:
        print(STYLE.dim(subtitle))
    print(STYLE.dim(rule()))
    extra = _header_lines(header)
    if extra:
        for line in extra:
            print(line)
        print(STYLE.dim(rule("·")))
    print()
    last_group = object()
    for index, item in enumerate(choices):
        if item.group and item.group != last_group:
            if index:
                print()
            print(STYLE.dim(item.group.upper()))
            last_group = item.group
        pointer = "❯" if index == cursor and highlight else " "
        number = STYLE.cyan(f"{index + 1:>2}")
        label = item.label
        if item.danger:
            label = STYLE.red(label)
        elif item.current:
            label = STYLE.green(label)
        if index == cursor and highlight:
            label = STYLE.bold(label)
        hint = STYLE.dim(f"  {item.hint}") if item.hint else ""
        mark = STYLE.green("  ●") if item.current else ""
        if index == cursor and highlight:
            print(STYLE.invert(f" {pointer} {index + 1:>2}  {item.label} ") + hint + mark)
        else:
            print(f" {pointer} {number}  {label}{mark}{hint}")
    print()
    print(STYLE.dim(rule("·")))
    help_bits = ["↑↓ move", "Enter select", "1-9 jump"]
    if allow_back:
        help_bits.append("Esc back")
    print(STYLE.dim("  " + "  ·  ".join(help_bits)))
    print()


def _pick_simple(
    title: str,
    choices: list[Choice],
    *,
    subtitle: str,
    header: str | list[str] | None,
    allow_back: bool,
) -> str | None:
    _draw_menu(
        title,
        choices,
        subtitle=subtitle,
        header=header,
        cursor=0,
        highlight=False,
        allow_back=allow_back,
    )
    prompt = "Select › " if not allow_back else "Select  ·  Enter goes back › "
    raw = input(STYLE.cyan(prompt)).strip()
    if allow_back and raw.lower() in {"", "b", "back", "q", "quit"}:
        return None
    matched = match_choice(raw, choices)
    if matched is None and raw:
        print(STYLE.red("Not a valid choice."))
        input(STYLE.dim("Press Enter to continue…"))
        return _pick_simple(
            title,
            choices,
            subtitle=subtitle,
            header=header,
            allow_back=allow_back,
        )
    return matched


def _pick_interactive(
    title: str,
    choices: list[Choice],
    *,
    subtitle: str,
    header: str | list[str] | None,
    allow_back: bool,
    cursor: int,
) -> str | None:
    cursor = max(0, min(cursor, len(choices) - 1))
    while True:
        _draw_menu(
            title,
            choices,
            subtitle=subtitle,
            header=header,
            cursor=cursor,
            highlight=True,
            allow_back=allow_back,
        )
        key = _read_key()
        if key in {"up", "k"}:
            cursor = (cursor - 1) % len(choices)
        elif key in {"down", "j"}:
            cursor = (cursor + 1) % len(choices)
        elif key in {"enter", "space"}:
            return choices[cursor].key
        elif key in {"esc", "q"} and allow_back:
            return None
        elif key.isdigit() and key != "0":
            index = int(key) - 1
            if index < len(choices):
                return choices[index].key
        else:
            matched = match_choice(key, choices)
            if matched is not None:
                return matched


def _read_key() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
        if char == "\x03":
            raise KeyboardInterrupt
        if char in {"\r", "\n"}:
            return "enter"
        if char == "\x1b":
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not ready:
                return "esc"
            rest = sys.stdin.read(1)
            if rest != "[":
                return "esc"
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not ready:
                return "esc"
            code = sys.stdin.read(1)
            return {"A": "up", "B": "down", "C": "down", "D": "up"}.get(code, "esc")
        if char == " ":
            return "space"
        return char
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
