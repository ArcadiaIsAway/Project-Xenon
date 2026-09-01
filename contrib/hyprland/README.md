# Optional Hyprland panic hotkey/WILL BE REMOVED FOR A MORE VERSATILE VERSION

Core Xenon does not need Hyprland. This only wires a chord to `xenon panic --gui`.

## Quick install

```bash
xenon install-trigger --desktop hyprland
hyprctl reload
```

## Manual

Add to `hyprland.conf` (or a sourced file):

```
bind = SUPER CTRL ALT SHIFT, X, exec, xenon panic --gui
```

If `xenon` is not on `PATH`, use the full interpreter path from your venv.

Print a ready-made bind:

```bash
xenon install-trigger --desktop hyprland --print-only
```

See `xenon.conf.example` in this directory.
