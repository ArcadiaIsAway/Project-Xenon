from __future__ import annotations

import errno
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from xenon.exclusions import ExclusionPolicy
from xenon.progress import ProbeDashboard
from xenon.vault import is_locked, xenon_dir
from xenon.walk import iter_walk_events


@dataclass
class FileProbeResult:
    path: Path
    relative: str
    ok: bool
    issues: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


@dataclass
class ScopeProbeReport:
    root: Path
    results: list[FileProbeResult]
    root_issues: list[str] = field(default_factory=list)
    root_hints: list[str] = field(default_factory=list)
    scanned: int = 0
    excluded_dirs: int = 0
    excluded_files: int = 0
    policy_summary: str = ""

    @property
    def ok_count(self) -> int:
        explicit_ok = sum(1 for item in self.results if item.ok)
        if explicit_ok:
            return explicit_ok
        return max(0, self.scanned - self.fail_count)

    @property
    def fail_count(self) -> int:
        return sum(1 for item in self.results if not item.ok)

    @property
    def passed(self) -> bool:
        return (
            not self.root_issues
            and self.fail_count == 0
            and self.scanned > 0
        )


def _errno_name(code: int | None) -> str:
    if code is None:
        return "unknown"
    return errno.errorcode.get(code, str(code))


def _is_readonly_fs(path: Path) -> bool | None:
    try:
        st = os.statvfs(path)
        return bool(st.f_flag & getattr(os, "ST_RDONLY", 1))
    except OSError:
        return None


def _diagnose_oserror(exc: OSError, *, target: str) -> tuple[str, list[str]]:
    code = exc.errno
    name = _errno_name(code)
    issue = f"{target}: {exc.strerror or exc} ({name})"
    hints: list[str] = []

    if code in {errno.EACCES, errno.EPERM}:
        hints.append("Permission denied — check ownership (`ls -l`) and ACLs.")
    elif code == errno.EROFS:
        hints.append("Filesystem is mounted read-only.")
    elif code == errno.ENOENT:
        hints.append("Path disappeared during the probe.")
    elif code == errno.EBUSY:
        hints.append("File is busy — close programs that have it open.")
    elif code == errno.ENOSPC:
        hints.append("No space left on device.")
    elif code == errno.EDQUOT:
        hints.append("Disk quota exceeded.")
    else:
        hints.append("Inspect with `ls -ld` on the file and its parent directory.")

    return issue, hints


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class _ParentCache:
    """Probe each parent directory at most once."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[bool, list[str], list[str]]] = {}

    def check(self, directory: Path) -> tuple[bool, list[str], list[str]]:
        key = str(directory)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        issues: list[str] = []
        hints: list[str] = []

        if not os.access(directory, os.W_OK | os.X_OK):
            issues.append(f"Parent directory not writable/executable: {directory}")
            hints.append(
                "In-place encryption creates a temp file then replaces the original."
            )
            result = (False, issues, hints)
            self._cache[key] = result
            return result

        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".xenon-probe-",
                suffix=".tmp",
                dir=directory,
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(b"x")
            os.unlink(tmp_name)
            tmp_name = None
            result = (True, issues, hints)
        except OSError as exc:
            issue, more = _diagnose_oserror(exc, target=f"parent {directory}")
            issues.append(issue)
            hints.extend(more)
            result = (False, issues, hints)
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

        self._cache[key] = result
        return result


def _probe_file_fast(path: Path) -> tuple[bool, list[str], list[str]]:
    """Lightweight readiness check — no full-file rewrite."""
    issues: list[str] = []
    hints: list[str] = []

    try:
        st = path.lstat()
    except OSError as exc:
        issue, more = _diagnose_oserror(exc, target="stat")
        return False, [issue], more

    if stat.S_ISLNK(st.st_mode):
        issues.append("Symlink skipped by walker; unexpected here.")
        return False, issues, hints

    if not stat.S_ISREG(st.st_mode):
        issues.append("Not a regular file.")
        return False, issues, hints

    if not os.access(path, os.R_OK):
        issues.append("Not readable.")
        hints.append("Encryption must read the current plaintext.")
        return False, issues, hints

    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        issue, more = _diagnose_oserror(exc, target="read")
        issues.append(issue)
        hints.extend(more)
        return False, issues, hints

    return True, issues, hints


def _policy_banner_lines(rules: ExclusionPolicy) -> list[str]:
    enabled = sorted(name for name, on in rules.optional_dirs.items() if on)
    lines = list(rules.protected_summary_lines())
    lines.append(
        "Optional dirs on: "
        + ", ".join(enabled[:14])
        + ("…" if len(enabled) > 14 else "")
    )
    if rules.custom_paths:
        lines.append(
            "Custom paths: "
            + ", ".join(rules.custom_paths[:6])
            + ("…" if len(rules.custom_paths) > 6 else "")
        )
    return lines


def probe_scope(
    root: Path,
    *,
    policy: ExclusionPolicy | None = None,
    show_progress: bool = True,
    keep_ok_results: bool = False,
) -> ScopeProbeReport:
    """Probe whether files under root can be encrypted in place."""
    root = root.expanduser().resolve()
    rules = policy or ExclusionPolicy()
    rules.ensure_running_cache()
    report = ScopeProbeReport(
        root=root,
        results=[],
        policy_summary=(
            f"system={rules.protect_system} project={rules.protect_project} "
            f"running={rules.protect_running} "
            f"optional_dirs={sum(1 for v in rules.optional_dirs.values() if v)} "
            f"custom={len(rules.custom_paths)}"
        ),
    )

    if not root.exists():
        report.root_issues.append(f"Target does not exist: {root}")
        report.root_hints.append("Set a valid directory in setup option 1.")
        return report

    if not root.is_dir():
        report.root_issues.append(f"Target is not a directory: {root}")
        return report

    if root == Path.home().resolve():
        report.root_hints.append(
            "Target is your home directory. Protected + optional exclusions "
            "apply; prefer a tighter folder when possible."
        )

    if _is_readonly_fs(root) is True:
        report.root_issues.append("Filesystem is mounted read-only.")
        report.root_hints.append("Remount read-write or choose another target.")

    if not os.access(root, os.R_OK | os.X_OK):
        report.root_issues.append("Cannot enter/list the target directory.")
        report.root_hints.append("Fix directory permissions on the target root.")

    parents = _ParentCache()
    parent_ok, parent_issues, parent_hints = parents.check(root)
    if not parent_ok:
        report.root_issues.extend(parent_issues)
        report.root_hints.extend(parent_hints)

    if is_locked(root):
        report.root_hints.append(
            "Target is already locked — unlock before re-locking."
        )

    meta = xenon_dir(root)
    if not meta.exists():
        try:
            meta.mkdir(parents=True, exist_ok=True)
            try:
                next(meta.iterdir())
            except StopIteration:
                meta.rmdir()
        except OSError as exc:
            issue, more = _diagnose_oserror(exc, target=".xenon metadata dir")
            report.root_issues.append(issue)
            report.root_hints.extend(more)

    # Unusable root — skip the walk; nothing to encrypt here yet.
    if report.root_issues and not os.access(root, os.R_OK | os.X_OK):
        return report

    dash = ProbeDashboard(root) if show_progress else None
    if dash is not None:
        dash.begin(_policy_banner_lines(rules))

    scanned = 0
    try:
        for event in iter_walk_events(root, policy=rules):
            if event.kind == "exclude_dir":
                report.excluded_dirs += 1
                if dash is not None:
                    dash.log_exclude_dir(event.path, event.reason)
                continue

            if event.kind == "exclude_file":
                report.excluded_files += 1
                if dash is not None:
                    dash.log_exclude_file(event.path, event.reason)
                continue

            path = event.path
            scanned += 1
            issues: list[str] = []
            hints: list[str] = []

            ok_parent, p_issues, p_hints = parents.check(path.parent)
            if not ok_parent:
                issues.extend(p_issues)
                hints.extend(p_hints)

            ok_file, f_issues, f_hints = _probe_file_fast(path)
            if not ok_file:
                issues.extend(f_issues)
                hints.extend(f_hints)

            ok = not issues
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = path.name

            if not ok:
                if dash is not None:
                    dash.log_blocked(path, _unique(issues), _unique(hints))
                report.results.append(
                    FileProbeResult(
                        path=path,
                        relative=relative,
                        ok=False,
                        issues=_unique(issues),
                        hints=_unique(hints),
                    )
                )
            else:
                if dash is not None:
                    dash.log_ok(path)
                if keep_ok_results:
                    report.results.append(
                        FileProbeResult(
                            path=path,
                            relative=relative,
                            ok=True,
                        )
                    )
    finally:
        if dash is not None:
            dash.close()

    report.scanned = scanned
    if scanned == 0 and not report.root_issues:
        report.root_issues.append("No files found in target (after exclusions).")
        report.root_hints.append(
            "Add files, widen the target, or adjust exclusions in setup."
        )

    return report
