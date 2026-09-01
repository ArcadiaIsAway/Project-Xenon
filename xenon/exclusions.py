from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from xenon.vault import XENON_DIRNAME

# ---------------------------------------------------------------------------
# Hard protection — always skipped. Encrypting these can break the OS/session.
# Each entry: (path, why)
# NOTE: /tmp is intentionally NOT a hard prefix (tests often live under /tmp).
# ---------------------------------------------------------------------------

PROTECTED_SYSTEM_PREFIXES: tuple[tuple[Path, str], ...] = tuple(
    (Path(path), why)
    for path, why in (
        ("/bin", "core user binaries"),
        ("/sbin", "core system binaries"),
        ("/usr", "system programs, libraries, share data"),
        ("/usr/local", "locally installed system software"),
        ("/lib", "shared libraries"),
        ("/lib64", "shared libraries (64-bit)"),
        ("/lib32", "shared libraries (32-bit)"),
        ("/etc", "system configuration"),
        ("/boot", "bootloader / kernel images"),
        ("/dev", "device nodes"),
        ("/proc", "process filesystem"),
        ("/sys", "sysfs / kernel interfaces"),
        ("/run", "runtime state (PID files, sockets)"),
        ("/var/run", "runtime state symlink/compat"),
        ("/var/lib", "system service state (pacman, systemd, …)"),
        ("/var/log", "system logs"),
        ("/var/cache", "system package/service caches"),
        ("/var/spool", "mail/print spools"),
        ("/var/lock", "lock files"),
        ("/opt", "optional system packages"),
        ("/snap", "Snap package runtime"),
        ("/srv", "service data roots"),
        ("/mnt", "mount points"),
        ("/media", "removable media mounts"),
        ("/lost+found", "filesystem recovery"),
    )
)

# Directory basenames that are always pruned (OS / runtime / package guts).
PROTECTED_DIR_NAMES: dict[str, str] = {
    XENON_DIRNAME: "Xenon lock metadata",
    "lost+found": "filesystem recovery",
    ".gvfs": "GVfs fuse mounts",
    ".dbus": "D-Bus user session state",
    "systemd": "systemd units/state",
    "containers": "container runtime data",
    "docker": "Docker runtime data",
    "podman": "Podman runtime data",
    "libvirt": "VM / libvirt runtime",
    "lxc": "LXC runtime",
    "flatpak": "Flatpak runtimes/apps",
    "snap": "Snap user/runtime data",
}


def _home_runtime_prefixes() -> tuple[tuple[Path, str], ...]:
    """Paths under $HOME that keep desktop/session/package tools alive."""
    home = Path.home()
    return (
        (home / ".config" / "xenon", "Xenon configuration"),
        (home / ".local" / "share" / "flatpak", "Flatpak runtimes (user)"),
        (home / ".local" / "share" / "containers", "Podman/containers (user)"),
        (home / ".local" / "share" / "Trash", "Trash metadata"),
        (home / ".local" / "state" / "wireplumber", "PipeWire/WirePlumber state"),
        (home / ".local" / "state" / "pipewire", "PipeWire state"),
        (home / ".cache" / "mesa_shader_cache", "GPU shader cache"),
        (home / ".cache" / "nvidia", "NVIDIA shader/cache"),
        (home / ".nv", "NVIDIA user state"),
        (home / ".steam", "Steam runtime"),
        (home / ".local" / "share" / "Steam", "Steam library metadata"),
        (home / ".var" / "app", "Flatpak app data"),
    )


# ---------------------------------------------------------------------------
# Optional catalog — user can toggle. Defaults ON (skip).
# ---------------------------------------------------------------------------

OPTIONAL_DIR_CATALOG: dict[str, str] = {
    # Caches / package managers
    ".cache": "User/app caches",
    ".npm": "npm cache",
    ".yarn": "Yarn cache",
    ".pnpm-store": "pnpm store",
    ".bun": "Bun cache/install",
    ".cargo": "Cargo registry/cache",
    ".rustup": "Rust toolchains",
    ".gradle": "Gradle cache",
    ".m2": "Maven repository",
    ".ivy2": "Ivy cache",
    ".sbt": "SBT cache",
    ".nuget": "NuGet packages",
    ".composer": "PHP Composer cache",
    ".gem": "Ruby gems",
    ".bundle": "Bundler path",
    ".pub-cache": "Dart pub cache",
    ".dartServer": "Dart analysis server",
    ".stack": "Haskell Stack",
    ".cpanm": "Perl cpanm",
    ".cache-loader": "Webpack cache-loader",
    # VCS
    ".git": "Git metadata",
    ".hg": "Mercurial metadata",
    ".svn": "Subversion metadata",
    ".bzr": "Bazaar metadata",
    # Language toolchains / envs
    ".venv": "Python virtualenvs",
    "venv": "Python virtualenvs (venv/)",
    ".tox": "Tox environments",
    ".nox": "Nox environments",
    ".direnv": "direnv cache",
    ".pyenv": "pyenv versions",
    ".rbenv": "rbenv versions",
    ".nvm": "Node version manager",
    ".fnm": "Fast Node Manager",
    ".asdf": "asdf version manager",
    ".sdkman": "SDKMAN candidates",
    ".local": "User-local installs (bin/lib/share) — toggle carefully",
    # Build / deps
    "node_modules": "Node dependencies",
    "bower_components": "Bower components",
    "__pycache__": "Python bytecode",
    ".pytest_cache": "Pytest cache",
    ".mypy_cache": "Mypy cache",
    ".ruff_cache": "Ruff cache",
    ".hypothesis": "Hypothesis examples",
    "target": "Rust/Java build output",
    "dist": "Build dist output",
    "build": "Build directories",
    "out": "Generic build output",
    "bin": "Local bin/ output dirs (name match)",
    "obj": "Object file dirs",
    "Debug": "IDE debug output",
    "Release": "IDE release output",
    "CMakeFiles": "CMake build files",
    ".terraform": "Terraform plugins/state local",
    ".vagrant": "Vagrant boxes",
    # Browsers / Electron
    "IndexedDB": "Browser IndexedDB",
    "GPUCache": "GPU caches",
    "Code Cache": "Browser code caches",
    "Cache": "Generic Cache dirs",
    "CacheStorage": "Cache API storage",
    "CachedData": "Editor/cached data",
    "Service Worker": "Browser service workers",
    "Service Worker Script Cache": "SW script cache",
    "blob_storage": "Browser blob storage",
    "DawnCache": "Chromium Dawn/WebGPU cache",
    "ShaderCache": "Browser shader cache",
    "GrShaderCache": "Graphics shader cache",
    "VideoDecodeStats": "Browser video decode stats",
    "File System": "Browser FileSystem API",
    "Platform Notifications": "Browser notifications DB",
    "Session Storage": "Browser session storage",
    "Local Storage": "Browser local storage",
    # Editors / IDEs
    ".idea": "JetBrains IDE project",
    ".vscode": "VS Code workspace data",
    ".vs": "Visual Studio cache",
    ".cursor": "Cursor IDE data",
    ".emacs.d": "Emacs user lisp/cache (name)",
    # Trash / backups
    "Trash": "Trash folders",
    ".Trash": "Trash folders",
    ".Trash-1000": "User trash (UID)",
    # Containers / VMs (name match extras)
    ".docker": "Docker user config/cache",
    "virtualbox": "VirtualBox data (name)",
    "VirtualBox VMs": "VirtualBox VM folders",
    # Games / large runtimes
    ".steam": "Steam (also hard-protected under $HOME/.steam)",
    "steamapps": "Steam apps library",
    "lutris": "Lutris runners/games",
    "wine": "Wine prefixes (name)",
    ".wine": "Default Wine prefix",
    # Misc noisy trees
    "log": "Generic log dirs",
    "logs": "Generic logs dirs",
    "tmp": "Generic tmp dirs",
    "temp": "Generic temp dirs",
    ".tmp": "Hidden tmp dirs",
    ".temp": "Hidden temp dirs",
    "var": "var trees when nested (name match) — toggle carefully",
}

OPTIONAL_DIR_CATALOG = dict(sorted(OPTIONAL_DIR_CATALOG.items()))

DEFAULT_OPTIONAL_ENABLED = frozenset(OPTIONAL_DIR_CATALOG.keys())

PROTECTED_CATEGORIES: tuple[tuple[str, str], ...] = (
    (
        "System roots",
        "OS binaries, libraries, config, boot, devices, and service state "
        "(/usr, /etc, /var/lib, /boot, …)",
    ),
    (
        "Runtime mounts",
        "Kernel/process interfaces and volatile runtime (/proc, /sys, /dev, /run)",
    ),
    (
        "Package & container runtimes",
        "Snap/Flatpak/Docker/Podman/libvirt trees that keep apps executing",
    ),
    (
        "Session / desktop runtimes",
        "User Flatpak/Steam/PipeWire/NVIDIA and similar under $HOME",
    ),
    (
        "Xenon itself",
        "This project, ~/.config/xenon, and the xenon launcher",
    ),
    (
        "Open files",
        "Anything currently mapped or opened by a running process (/proc scan)",
    ),
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def xenon_config_dir() -> Path:
    return Path.home() / ".config" / "xenon"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def collect_running_paths() -> set[Path]:
    """Best-effort set of files currently mapped/opened by processes."""
    found: set[Path] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return found

    try:
        pids = [entry for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError:
        return found

    for pid_dir in pids:
        exe = pid_dir / "exe"
        try:
            found.add(exe.resolve(strict=True))
        except OSError:
            pass

        fd_dir = pid_dir / "fd"
        try:
            descriptors = list(fd_dir.iterdir())
        except OSError:
            continue

        for fd in descriptors:
            try:
                target = Path(os.readlink(fd))
            except OSError:
                continue
            if str(target).startswith(("pipe:", "socket:", "anon_inode:")):
                continue
            try:
                found.add(target.resolve())
            except OSError:
                try:
                    found.add(target)
                except OSError:
                    pass

    return found


def _default_optional_dirs() -> dict[str, bool]:
    return {name: True for name in DEFAULT_OPTIONAL_ENABLED}


@dataclass
class ExclusionPolicy:
    """Hard protections + user-toggleable optional rules."""

    protect_system: bool = True
    protect_project: bool = True
    protect_running: bool = True
    optional_dirs: dict[str, bool] = field(default_factory=_default_optional_dirs)
    custom_paths: list[str] = field(default_factory=list)
    _running: set[Path] | None = field(default=None, repr=False)
    _project: Path = field(default_factory=project_root, repr=False)
    _config_dir: Path = field(default_factory=xenon_config_dir, repr=False)
    _home_runtime: tuple[tuple[Path, str], ...] = field(
        default_factory=_home_runtime_prefixes,
        repr=False,
    )

    @classmethod
    def from_config(cls, config: dict | None) -> ExclusionPolicy:
        data = (config or {}).get("exclusions") or {}
        optional = {name: True for name in DEFAULT_OPTIONAL_ENABLED}
        configured = data.get("optional_dirs")
        if isinstance(configured, dict):
            for name, enabled in configured.items():
                optional[str(name)] = bool(enabled)

        custom = data.get("custom_paths") or []
        if not isinstance(custom, list):
            custom = []

        # Always-on hard protections — config cannot disable them.
        return cls(
            protect_system=True,
            protect_project=True,
            protect_running=True,
            optional_dirs=optional,
            custom_paths=[str(item) for item in custom],
        )

    def to_config(self) -> dict:
        return {
            "protect_system": self.protect_system,
            "protect_project": self.protect_project,
            "protect_running": self.protect_running,
            "optional_dirs": dict(sorted(self.optional_dirs.items())),
            "custom_paths": list(self.custom_paths),
        }

    def ensure_running_cache(self) -> set[Path]:
        if not self.protect_running:
            return set()
        if self._running is None:
            self._running = collect_running_paths()
        return self._running

    def refresh_running_cache(self) -> set[Path]:
        self._running = None
        return self.ensure_running_cache()

    def custom_resolved(self, root: Path | None = None) -> list[Path]:
        resolved: list[Path] = []
        for raw in self.custom_paths:
            path = Path(raw).expanduser()
            if not path.is_absolute() and root is not None:
                path = (root / path).resolve()
            else:
                try:
                    path = path.resolve()
                except OSError:
                    path = path.expanduser()
            resolved.append(path)
        return resolved

    def is_protected_path(self, path: Path) -> str | None:
        """Return reason string if path is hard-protected, else None."""
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        if self.protect_system:
            for prefix, why in PROTECTED_SYSTEM_PREFIXES:
                if _is_relative_to(resolved, prefix):
                    return f"protected system ({prefix}) — {why}"

            for part in resolved.parts:
                why = PROTECTED_DIR_NAMES.get(part)
                if why and part != XENON_DIRNAME:
                    return f"protected runtime ({part}) — {why}"

            for prefix, why in self._home_runtime:
                try:
                    prefix_resolved = prefix.resolve()
                except OSError:
                    prefix_resolved = prefix
                if _is_relative_to(resolved, prefix_resolved):
                    return f"protected session runtime — {why}"

        if self.protect_project:
            if _is_relative_to(resolved, self._project):
                return "protected — Xenon project files"
            if _is_relative_to(resolved, self._config_dir):
                return "protected — Xenon config directory"
            local_bin = Path.home() / ".local" / "bin" / "xenon"
            try:
                if resolved == local_bin.resolve():
                    return "protected — Xenon launcher"
            except OSError:
                if resolved == local_bin:
                    return "protected — Xenon launcher"

        if self.protect_running:
            running = self.ensure_running_cache()
            if resolved in running or path in running:
                return "protected — open by a running process"

        return None

    def is_custom_excluded(self, path: Path, root: Path) -> str | None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        for custom in self.custom_resolved(root):
            if resolved == custom or _is_relative_to(resolved, custom):
                return f"custom exclusion ({custom})"
        return None

    def prune_reason(self, name: str, dirpath: Path, root: Path) -> str | None:
        if name == XENON_DIRNAME:
            return f"protected — {PROTECTED_DIR_NAMES[XENON_DIRNAME]}"

        why = PROTECTED_DIR_NAMES.get(name)
        if why and self.protect_system:
            return f"protected runtime ({name}) — {why}"

        if self.optional_dirs.get(name, False):
            desc = OPTIONAL_DIR_CATALOG.get(name, "user optional rule")
            return f"optional exclusion ({name}) — {desc}"

        candidate = dirpath / name
        protected = self.is_protected_path(candidate)
        if protected:
            return protected
        custom = self.is_custom_excluded(candidate, root)
        if custom:
            return custom
        return None

    def should_prune_dir(self, name: str, dirpath: Path, root: Path) -> bool:
        return self.prune_reason(name, dirpath, root) is not None

    def should_skip_file(self, path: Path, root: Path) -> str | None:
        reason = self.is_protected_path(path)
        if reason:
            return reason
        return self.is_custom_excluded(path, root)

    def protected_summary_lines(self) -> list[str]:
        return [f"{title}: {detail}" for title, detail in PROTECTED_CATEGORIES]
