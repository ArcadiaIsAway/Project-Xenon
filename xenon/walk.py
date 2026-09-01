from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from xenon.exclusions import ExclusionPolicy
from xenon.progress import Progress
from xenon.vault import XENON_DIRNAME


@dataclass(frozen=True)
class WalkEvent:
    kind: str  # "file" | "exclude_dir" | "exclude_file"
    path: Path
    reason: str = ""


def iter_walk_events(
    root: Path,
    *,
    policy: ExclusionPolicy | None = None,
) -> Iterator[WalkEvent]:
    """Stream files and exclusion decisions for transparent scanning."""
    root = root.expanduser().resolve()
    rules = policy or ExclusionPolicy()
    rules.ensure_running_cache()

    if not root.is_dir():
        return

    def on_error(_exc: OSError) -> None:
        return None

    for dirpath, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=on_error,
        followlinks=False,
    ):
        current = Path(dirpath)
        if current.name == XENON_DIRNAME:
            dirnames[:] = []
            yield WalkEvent("exclude_dir", current, "Xenon metadata (.xenon)")
            continue

        kept: list[str] = []
        for name in sorted(dirnames):
            reason = rules.prune_reason(name, current, root)
            if reason:
                yield WalkEvent("exclude_dir", current / name, reason)
            else:
                kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            path = current / name
            try:
                st = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            reason = rules.should_skip_file(path, root)
            if reason:
                yield WalkEvent("exclude_file", path, reason)
                continue
            yield WalkEvent("file", path)


def iter_files(
    root: Path,
    *,
    policy: ExclusionPolicy | None = None,
    progress: Progress | None = None,
) -> Iterator[Path]:
    """Stream regular files under root using the exclusion policy."""
    root = root.expanduser().resolve()
    for event in iter_walk_events(root, policy=policy):
        if event.kind != "file":
            continue
        if progress is not None:
            progress.update(1, suffix=_short(event.path, root))
        yield event.path


def collect_files(
    root: Path,
    *,
    policy: ExclusionPolicy | None = None,
    label: str = "Scanning",
    show_progress: bool = True,
) -> list[Path]:
    progress = Progress(label) if show_progress else None
    files: list[Path] = []
    try:
        for path in iter_files(root, policy=policy, progress=progress):
            files.append(path)
    finally:
        if progress is not None:
            progress.close(suffix=f"{len(files):,} files")
    return files


def collect_directories(
    root: Path,
    *,
    policy: ExclusionPolicy | None = None,
) -> list[Path]:
    """Return non-excluded directories under root (not including root)."""
    root = root.expanduser().resolve()
    rules = policy or ExclusionPolicy()
    rules.ensure_running_cache()
    found: list[Path] = []

    if not root.is_dir():
        return found

    def on_error(_exc: OSError) -> None:
        return None

    for dirpath, dirnames, _filenames in os.walk(
        root,
        topdown=True,
        onerror=on_error,
        followlinks=False,
    ):
        current = Path(dirpath)
        if current.name == XENON_DIRNAME:
            dirnames[:] = []
            continue

        kept: list[str] = []
        for name in sorted(dirnames):
            if rules.prune_reason(name, current, root):
                continue
            child = current / name
            try:
                st = child.lstat()
            except OSError:
                continue
            if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
                continue
            kept.append(name)
            found.append(child)
        dirnames[:] = kept

    return found


def _short(path: Path, root: Path, limit: int = 42) -> str:
    try:
        text = path.relative_to(root).as_posix()
    except ValueError:
        text = path.name
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1) :]
