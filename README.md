# Project Xenon

Portable in-place directory encryption with optional panic lockdown.

## Install

```bash
./scripts/install.sh
```

Puts `xenon` on `PATH` via `~/.local/bin` (venv + editable install).

## Setup

```bash
xenon setup
```

Interactive TUI: manage encryption targets, password (session only), cipher,
optional key file, exclusions, and readiness probe.

## Commands

| Command | Purpose |
|---------|---------|
| `xenon lock [dir]` | Encrypt in place (default: all configured targets) |
| `xenon unlock [dir]` | Decrypt in place |
| `xenon verify [dir]` | Check ciphertext integrity |
| `xenon check [dir]` | Probe read/rewrite readiness |
| `xenon panic` | Lock all targets, then screen-lock + session kill |
| `xenon install-trigger` | Optional Hyprland hotkey → panic | 

## Hyprland trigger will be removed/changed for more versatility

## Exclusions

Always protected: system paths, Xenon itself, and running process files.

##Will add setting for killing and encrypting running processes in future versions

Optional directory-name toggles (`.cache`, `node_modules`, `.git`, …) and custom
paths are managed in setup menu **6** or `exclusions` in config.

See `examples/config.json` and `contrib/hyprland/` for samples.
