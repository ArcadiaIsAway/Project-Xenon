# Optional panic hotkey

Core Xenon does not need a compositor. `xenon install-trigger` detects the
running desktop and binds your panic chord to a PATH-independent wrapper:

`~/.local/lib/xenon/panic-trigger` → `xenon panic --trigger`

The chord is the authorization. There is no password prompt.

## Quick install

```bash
xenon install-trigger
```

On Hyprland this writes `~/.config/hypr/xenon.conf` and sources it from
`hyprland.conf`. Reload if the bind is not live:

```bash
hyprctl reload
```

## Manual (Hyprland)

If you prefer to bind it yourself, use the wrapper — not `xenon` on PATH:

```
bind = SUPER CTRL ALT SHIFT, X, exec, ~/.local/lib/xenon/panic-trigger
```

Print a ready-made snippet for the detected desktop:

```bash
xenon install-trigger --print-only
```
