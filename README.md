# Project Xenon

Portable in-place directory encryption with optional panic lockdown.

## Install

```bash
./scripts/install.sh
```

Works across distros: finds Python 3.10+, creates a virtualenv, and puts a
`xenon` command on PATH (prefers a writable directory already on PATH,
otherwise `~/.local/bin` and wires it into your shells automatically).

If Python or `venv` is missing, the script installs the distro packages.

To update PATH in the *current* terminal as well:

```bash
source ./scripts/install.sh
```

## Setup

```bash
xenon setup
```

Interactive TUI: arrow keys or numbers to select. Manage encryption
targets, password, cipher, optional key file, exclusions, readiness
probe, and the panic hotkey.

## Commands

| Command | Purpose |
|---------|---------|
| `xenon lock [dir]` | Encrypt in place (default: all configured targets) |
| `xenon unlock [dir]` | Decrypt in place |
| `xenon verify [dir]` | Check ciphertext integrity |
| `xenon check [dir]` | Probe read/rewrite readiness |
| `xenon panic --trigger` | Immediate panic (hotkey path; no password) |
| `xenon panic --preview` | Play panic effects only (no lock, wipe, or shutdown) |
| `xenon install-trigger` | Bind the panic chord on the current desktop |

## Panic hotkey

Set the key combination in setup menu **7**, then install the binding.
Hitting that chord runs panic immediately — it does not ask for a password.
The same menu picks a preset (silent, cinematic, rave, TTY storm, finale)
or lets you customize playback: animation, effect order, music file,
wait-for-track, power off after the track, a visual console look, and script flash.
**Preview spectacle** (or `xenon panic --preview`) plays that sequence
fullscreen without locking files, killing the session, or powering off.
Press Enter or Esc to exit the preview.

`install-trigger` writes `~/.local/lib/xenon/panic-trigger`, an absolute
wrapper around this Xenon install, so compositor sessions do not need
`xenon` on `PATH`. Hyprland, Sway, i3, niri, river, GNOME, KDE, XFCE,
Cinnamon, MATE, and LXQt are detected automatically; otherwise bind the
wrapper yourself.

```bash
xenon install-trigger
hyprctl reload   # Hyprland; other desktops usually apply live
```

## Exclusions

Always protected: system paths, Xenon itself, and running process files.

Optional directory-name toggles (`.cache`, `node_modules`, `.git`, …) and custom
paths are managed in setup menu **6** or `exclusions` in config.

See `examples/config.json` for a sample config.
