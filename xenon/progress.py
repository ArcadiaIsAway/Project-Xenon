from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import TextIO


class Progress:
    """Lightweight stderr progress display (bar when total known, counter otherwise)."""

    def __init__(
        self,
        label: str,
        *,
        total: int | None = None,
        stream: TextIO | None = None,
        width: int | None = None,
    ) -> None:
        self.label = label
        self.total = total
        self.n = 0
        self.stream = stream or sys.stderr
        self.width = width or min(36, max(16, shutil.get_terminal_size((80, 24)).columns - 40))
        self._last_draw = 0.0
        self._enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self._closed = False

    def update(self, step: int = 1, *, suffix: str = "") -> None:
        if self._closed:
            return
        self.n += step
        now = time.monotonic()
        if self._enabled and (now - self._last_draw) < 0.05:
            if self.total is None or self.n < self.total:
                return
        self._last_draw = now
        self._draw(suffix=suffix)

    def set_total(self, total: int) -> None:
        self.total = total
        self._draw()

    def _draw(self, *, suffix: str = "") -> None:
        if not self._enabled:
            return

        if self.total and self.total > 0:
            ratio = min(1.0, self.n / self.total)
            filled = int(self.width * ratio)
            bar = "█" * filled + "░" * (self.width - filled)
            line = f"\r{self.label} [{bar}] {self.n}/{self.total}"
        else:
            line = f"\r{self.label} · {self.n:,}"

        if suffix:
            line = f"{line} · {suffix}"

        cols = shutil.get_terminal_size((80, 24)).columns
        line = line[: cols - 1].ljust(min(cols - 1, len(line) + 8))
        self.stream.write(line)
        self.stream.flush()

    def close(self, *, suffix: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        if not self._enabled:
            msg = f"{self.label} · {self.n:,}"
            if self.total is not None:
                msg = f"{self.label} · {self.n:,}/{self.total:,}"
            if suffix:
                msg = f"{msg} · {suffix}"
            print(msg, file=self.stream)
            return
        self._draw(suffix=suffix or "done")
        self.stream.write("\n")
        self.stream.flush()


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class ProbeDashboard:
    """Live probe UI: scrolling transparency logs + sticky status line."""

    def __init__(
        self,
        root: Path,
        *,
        out: TextIO | None = None,
        err: TextIO | None = None,
    ) -> None:
        self.root = Path(root)
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.tty = bool(getattr(self.err, "isatty", lambda: False)())
        self.color = self.tty and not os.environ.get("NO_COLOR")
        self.checked = 0
        self.ok = 0
        self.blocked = 0
        self.excluded_dirs = 0
        self.excluded_files = 0
        self.current = ""
        self._spin_i = 0
        self._start = time.monotonic()
        self._last_draw = 0.0
        self._closed = False

    def _c(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _rate(self) -> str:
        elapsed = max(0.001, time.monotonic() - self._start)
        return f"{self.checked / elapsed:,.0f}/s"

    def _clear_status(self) -> None:
        if self.tty:
            self.err.write("\r\033[2K")
            self.err.flush()

    def _draw(self, *, force: bool = False) -> None:
        if self._closed or not self.tty:
            return
        now = time.monotonic()
        if not force and (now - self._last_draw) < 0.07:
            return
        self._last_draw = now
        self._spin_i = (self._spin_i + 1) % len(_SPIN)
        spin = self._c("96", _SPIN[self._spin_i])
        excl = self.excluded_dirs + self.excluded_files
        current = self.current or "…"
        cols = shutil.get_terminal_size((80, 24)).columns
        left = (
            f"\r{spin} "
            f"{self._c('1', 'probe')}  "
            f"{self._c('92', f'{self.ok:,}✓')}  "
            f"{self._c('91', f'{self.blocked:,}✗')}  "
            f"{self._c('2', f'{excl:,}⊘')}  "
            f"{self._c('2', self._rate())}  "
            f"{self._c('2', '▸')} {current}"
        )
        visible = (
            f"• probe  {self.ok:,}✓  {self.blocked:,}✗  {excl:,}⊘  "
            f"{self._rate()}  ▸ {current}"
        )
        if len(visible) > cols - 1:
            trim = len(visible) - (cols - 1)
            current = "…" + current[-(max(8, len(current) - trim)) :]
            left = (
                f"\r{spin} "
                f"{self._c('1', 'probe')}  "
                f"{self._c('92', f'{self.ok:,}✓')}  "
                f"{self._c('91', f'{self.blocked:,}✗')}  "
                f"{self._c('2', f'{excl:,}⊘')}  "
                f"{self._c('2', self._rate())}  "
                f"{self._c('2', '▸')} {current}"
            )
        self.err.write(left + "\033[K")
        self.err.flush()

    def begin(self, policy_lines: list[str] | None = None) -> None:
        print(self._c("1", "Live feed"), file=self.out)
        print(
            self._c("2", "⊘ excluded   ✗ blocked   ✓ counted toward encryption"),
            file=self.out,
        )
        if policy_lines:
            for line in policy_lines:
                print(self._c("2", f"  · {line}"), file=self.out)
        print(
            self._c("2", "─" * min(72, shutil.get_terminal_size((80, 24)).columns)),
            file=self.out,
        )
        self.out.flush()
        self._draw(force=True)

    def log_exclude_dir(self, path: Path, reason: str) -> None:
        self.excluded_dirs += 1
        self._clear_status()
        rel = self._rel(path)
        print(
            f"  {self._c('2', '⊘')} {self._c('95', 'dir ')} {rel}  "
            f"{self._c('2', '—')} {self._c('2', reason)}",
            file=self.out,
            flush=True,
        )
        self.current = rel
        self._draw(force=True)

    def log_exclude_file(self, path: Path, reason: str) -> None:
        self.excluded_files += 1
        self._clear_status()
        rel = self._rel(path)
        print(
            f"  {self._c('2', '⊘')} {self._c('33', 'file')} {rel}  "
            f"{self._c('2', '—')} {self._c('2', reason)}",
            file=self.out,
            flush=True,
        )
        self.current = rel
        self._draw(force=True)

    def log_blocked(self, path: Path, issues: list[str], hints: list[str]) -> None:
        self.blocked += 1
        self.checked += 1
        self._clear_status()
        rel = self._rel(path)
        print(f"  {self._c('91;1', '✗')} {rel}", file=self.out, flush=True)
        for issue in issues:
            print(f"      {self._c('91', '•')} {issue}", file=self.out, flush=True)
        for hint in hints:
            print(f"      {self._c('2', '↳')} {hint}", file=self.out, flush=True)
        self.current = rel
        self._draw(force=True)

    def log_ok(self, path: Path) -> None:
        self.ok += 1
        self.checked += 1
        self.current = self._rel(path)
        self._draw()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._clear_status()
        excl = self.excluded_dirs + self.excluded_files
        elapsed = max(0.001, time.monotonic() - self._start)
        summary = (
            f"{self._c('1', 'Done')}  "
            f"{self._c('92', f'{self.ok:,} ok')}  "
            f"{self._c('91', f'{self.blocked:,} blocked')}  "
            f"{self._c('2', f'{excl:,} excluded')}  "
            f"{self._c('2', f'{elapsed:.1f}s')}"
        )
        print(summary, file=self.out, flush=True)
        if self.tty:
            self.err.write("\n")
            self.err.flush()
