#!/usr/bin/env bash
# Cross-distro installer: put the xenon CLI on PATH without manual setup.
#
# Usage:
#   ./scripts/install.sh
#   source ./scripts/install.sh    # also updates PATH in the current shell
#
# Optional:
#   PYTHON=/usr/bin/python3.12 ./scripts/install.sh
#   ./scripts/install.sh --bin-dir ~/.local/bin

if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
  echo "Xenon installer requires bash." >&2
  exit 1
fi

_XENON_SOURCED=0
if [[ "${BASH_SOURCE[0]:-}" != "$0" ]]; then
  _XENON_SOURCED=1
fi

_XENON_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_xenon_die() {
  echo "ERROR: $*" >&2
  return 1
}

_xenon_python_ok() {
  local bin="$1"
  [[ -x "$bin" ]] || return 1
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}

_xenon_find_python() {
  local candidate
  if [[ -n "${PYTHON:-}" ]]; then
    if _xenon_python_ok "${PYTHON}"; then
      echo "${PYTHON}"
      return 0
    fi
    if command -v "${PYTHON}" >/dev/null 2>&1 && _xenon_python_ok "$(command -v "${PYTHON}")"; then
      command -v "${PYTHON}"
      return 0
    fi
  fi
  for candidate in \
    python3.14 python3.13 python3.12 python3.11 python3.10 \
    python3 python
  do
    if command -v "${candidate}" >/dev/null 2>&1 \
      && _xenon_python_ok "$(command -v "${candidate}")"; then
      command -v "${candidate}"
      return 0
    fi
  done
  return 1
}

_xenon_os_id() {
  (
    if [[ -r /etc/os-release ]]; then
      # shellcheck disable=SC1091
      . /etc/os-release
      echo "${ID:-}:${ID_LIKE:-}"
    else
      echo "$(uname -s | tr '[:upper:]' '[:lower:]'):"
    fi
  )
}

_xenon_python_packages() {
  local id
  id="$(_xenon_os_id)"
  case "${id}" in
    arch:*|manjaro:*|endeavouros:*|cachyos:*|garuda:*|*:arch*)
      echo python python-pip
      ;;
    debian:*|ubuntu:*|linuxmint:*|pop:*|raspbian:*|kali:*|*:debian*)
      echo python3 python3-venv python3-pip
      ;;
    fedora:*|rhel:*|centos:*|rocky:*|almalinux:*|*:fedora*|*:rhel*)
      echo python3 python3-pip
      ;;
    opensuse*|sles:*|*:suse*)
      echo python3 python3-pip python3-virtualenv
      ;;
    alpine:*)
      echo python3 py3-pip py3-virtualenv py3-cryptography
      ;;
    void:*)
      echo python3 python3-pip
      ;;
    solus:*)
      echo python3 python3-pip
      ;;
    *)
      case "$(uname -s)" in
        Darwin) echo python ;;
        *) echo python3 ;;
      esac
      ;;
  esac
}

_xenon_pkg_install() {
  echo "Installing packages: $*"
  if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm "$@"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y "$@"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y "$@"
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y "$@"
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper --non-interactive install "$@"
  elif command -v apk >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then
      apk add "$@"
    else
      sudo apk add "$@"
    fi
  elif command -v xbps-install >/dev/null 2>&1; then
    sudo xbps-install -Sy "$@"
  elif command -v eopkg >/dev/null 2>&1; then
    sudo eopkg install -y "$@"
  elif command -v brew >/dev/null 2>&1; then
    brew install "$@"
  elif command -v pkg >/dev/null 2>&1; then
    if [[ -n "${TERMUX_VERSION:-}" ]] || [[ "$(id -u)" -eq 0 ]]; then
      pkg install -y "$@"
    else
      sudo pkg install -y "$@"
    fi
  else
    return 1
  fi
}

_xenon_dir_writable() {
  local dir="$1"
  [[ -n "$dir" ]] || return 1
  if [[ -d "$dir" ]]; then
    [[ -w "$dir" ]]
    return
  fi
  case "$dir" in
    "${HOME}"/*)
      mkdir -p "$dir" 2>/dev/null && [[ -w "$dir" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

_xenon_safe_bindir() {
  local dir="$1"
  [[ "$dir" == /* ]] || return 1
  case "$dir" in
    /bin | /sbin | /usr/bin | /usr/sbin | /usr/local/sbin) return 1 ;;
    "${_XENON_ROOT}/.venv" | "${_XENON_ROOT}/.venv"/*) return 1 ;;
    */pyenv/shims | */pyenv/shims/) return 1 ;;
    */nodejs/bin | */nvm/*/bin) return 1 ;;
  esac
  return 0
}

# Prefer a directory that is already on PATH and writable so `xenon` works
# in this terminal without the user editing anything.
_xenon_pick_bindir() {
  local override="$1"
  local dir
  local xdg="${XDG_BIN_HOME:-}"
  local home_local="${HOME}/.local/bin"
  local home_bin="${HOME}/bin"

  if [[ -n "$override" ]]; then
    mkdir -p "$override"
    echo "$override"
    return 0
  fi

  _xenon_on_path() {
    case ":${PATH}:" in
      *":$1:"*) return 0 ;;
      *) return 1 ;;
    esac
  }

  if [[ -n "$xdg" ]] && _xenon_on_path "$xdg" && _xenon_safe_bindir "$xdg" && _xenon_dir_writable "$xdg"; then
    echo "$xdg"
    return 0
  fi
  if _xenon_on_path "$home_local" && _xenon_safe_bindir "$home_local" && _xenon_dir_writable "$home_local"; then
    echo "$home_local"
    return 0
  fi
  if _xenon_on_path "$home_bin" && _xenon_safe_bindir "$home_bin" && _xenon_dir_writable "$home_bin"; then
    echo "$home_bin"
    return 0
  fi

  local rest="${PATH}"
  while [[ -n "$rest" ]]; do
    dir="${rest%%:*}"
    if [[ "$rest" == *:* ]]; then
      rest="${rest#*:}"
    else
      rest=""
    fi
    [[ -n "$dir" ]] || continue
    _xenon_safe_bindir "$dir" || continue
    if _xenon_dir_writable "$dir"; then
      echo "$dir"
      return 0
    fi
  done

  local fallback="${xdg:-$home_local}"
  if mkdir -p "$fallback" 2>/dev/null && [[ -w "$fallback" ]]; then
    echo "$fallback"
    return 0
  fi
  mkdir -p "$home_local"
  echo "$home_local"
}

_xenon_path_snippet() {
  local dir="$1"
  cat <<EOF
xenon_bin="${dir}"
case ":\${PATH}:" in
  *":\${xenon_bin}:"*) ;;
  *) PATH="\${xenon_bin}:\${PATH}" ;;
esac
export PATH
unset xenon_bin
EOF
}

_xenon_upsert_block() {
  local file="$1"
  local body="$2"
  local start="# >>> xenon path >>>"
  local end="# <<< xenon path <<<"
  local parent
  parent="$(dirname "$file")"
  mkdir -p "$parent"
  touch "$file"

  local filtered
  filtered="$(mktemp)"
  awk -v s="$start" -v e="$end" '
    $0 == s { skip = 1; next }
    $0 == e { skip = 0; next }
    skip != 1 { print }
  ' "$file" > "$filtered"

  if [[ -s "$filtered" ]] && [[ "$(tail -c 1 "$filtered" | wc -c)" -ne 0 ]]; then
    printf '\n' >> "$filtered"
  fi
  {
    cat "$filtered"
    printf '%s\n%s\n%s\n' "$start" "$body" "$end"
  } > "$file"
  rm -f "$filtered"
}

_xenon_ensure_path() {
  local bindir="$1"
  local snippet
  snippet="$(_xenon_path_snippet "$bindir")"

  echo "Making sure ${bindir} stays on PATH for new shells"

  _xenon_upsert_block "${HOME}/.profile" "$snippet"

  local rc
  for rc in \
    "${HOME}/.bashrc" \
    "${HOME}/.bash_profile" \
    "${HOME}/.zshrc" \
    "${HOME}/.zprofile" \
    "${HOME}/.zshenv" \
    "${HOME}/.kshrc"
  do
    if [[ -f "$rc" ]] || [[ "$rc" == "${HOME}/.bashrc" && "${SHELL:-}" == *bash* ]] \
      || [[ "$rc" == "${HOME}/.zshrc" && "${SHELL:-}" == *zsh* ]]; then
      _xenon_upsert_block "$rc" "$snippet"
    fi
  done

  if [[ "${SHELL:-}" == *bash* && ! -f "${HOME}/.bashrc" ]]; then
    _xenon_upsert_block "${HOME}/.bashrc" "$snippet"
  fi
  if [[ "${SHELL:-}" == *zsh* && ! -f "${HOME}/.zshrc" ]]; then
    _xenon_upsert_block "${HOME}/.zshrc" "$snippet"
  fi

  local fish_dir="${HOME}/.config/fish/conf.d"
  mkdir -p "$fish_dir"
  cat > "${fish_dir}/xenon-path.fish" <<EOF
# Added by Project Xenon installer
fish_add_path -P ${bindir}
EOF

  local plasma_env="${HOME}/.config/plasma-workspace/env"
  if [[ -d "${HOME}/.config/plasma-workspace" ]] || [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then
    mkdir -p "$plasma_env"
    _xenon_upsert_block "${plasma_env}/xenon-path.sh" "$snippet"
  fi

  local uwsm_env="${HOME}/.config/uwsm/env"
  if [[ -e "$uwsm_env" ]] || [[ -d "${HOME}/.config/uwsm" ]]; then
    _xenon_upsert_block "$uwsm_env" "$snippet"
  fi
}

_xenon_write_command() {
  local bindir="$1"
  local venv="$2"
  local target="${bindir}/xenon"
  mkdir -p "$bindir"
  cat > "$target" <<EOF
#!/bin/sh
exec "${venv}/bin/python" -m xenon "\$@"
EOF
  chmod +x "$target"
  echo "$target"
}

_xenon_ensure_venv() {
  local python="$1"
  local venv="$2"
  local extra=()

  if [[ -f /etc/alpine-release ]]; then
    extra+=(--system-site-packages)
  fi

  if command -v uv >/dev/null 2>&1; then
    echo "Creating virtualenv with uv…"
    uv venv "$venv" --python "$python"
    echo "Installing package…"
    uv pip install --python "${venv}/bin/python" -e "${_XENON_ROOT}"
    return 0
  fi

  if [[ ! -d "$venv" ]]; then
    echo "Creating virtualenv…"
    if ! "$python" -m venv "${extra[@]}" "$venv" 2>/dev/null; then
      echo "Python venv module missing; installing distro packages…"
      # shellcheck disable=SC2046
      if ! _xenon_pkg_install $(_xenon_python_packages); then
        _xenon_die "Could not create a virtualenv. Install Python 3.10+ and the venv/pip packages for your distro."
        return 1
      fi
      python="$(_xenon_find_python)" || {
        _xenon_die "Python 3.10+ is still not available after package install."
        return 1
      }
      "$python" -m venv "${extra[@]}" "$venv"
    fi
  fi

  if [[ ! -x "${venv}/bin/python" ]]; then
    _xenon_die "Virtualenv is missing ${venv}/bin/python"
    return 1
  fi

  if ! "${venv}/bin/python" -m pip --version >/dev/null 2>&1; then
    "${venv}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi

  echo "Installing package…"
  if "${venv}/bin/python" -m pip --version >/dev/null 2>&1; then
    "${venv}/bin/python" -m pip install -U pip >/dev/null
    "${venv}/bin/python" -m pip install -e "${_XENON_ROOT}"
  else
    _xenon_die "pip is not available in the virtualenv."
    return 1
  fi
}

_xenon_usage() {
  cat <<EOF
Usage: ./scripts/install.sh [--bin-dir DIR] [--help]

Installs Xenon into a project virtualenv and places a \`xenon\` command on PATH.

  --bin-dir DIR   Install the xenon command here (default: first writable PATH dir,
                  otherwise ~/.local/bin)
  --help          Show this help

You can also run:  source ./scripts/install.sh
to update PATH in the current shell.
EOF
}

_xenon_main() {
  local bin_override=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --bin-dir)
        bin_override="${2:-}"
        [[ -n "$bin_override" ]] || {
          _xenon_die "--bin-dir needs a directory"
          return 1
        }
        shift 2
        ;;
      --bin-dir=*)
        bin_override="${1#--bin-dir=}"
        shift
        ;;
      -h | --help)
        _xenon_usage
        return 0
        ;;
      *)
        _xenon_die "Unknown option: $1"
        return 1
        ;;
    esac
  done

  local venv="${_XENON_ROOT}/.venv"
  local python=""
  local bindir=""
  local command_path=""

  echo "Project: ${_XENON_ROOT}"

  python="$(_xenon_find_python)" || python=""
  if [[ -z "$python" ]]; then
    echo "Python 3.10+ not found; installing distro packages…"
    # shellcheck disable=SC2046
    if ! _xenon_pkg_install $(_xenon_python_packages); then
      _xenon_die "Install Python 3.10+ and re-run. Example packages: $(_xenon_python_packages)"
      return 1
    fi
    python="$(_xenon_find_python)" || {
      _xenon_die "Python 3.10+ is still not available."
      return 1
    }
  fi
  echo "Python: ${python}"

  if [[ "${XENON_SKIP_VENV:-}" == 1 && -x "${venv}/bin/python" ]]; then
    echo "Reusing existing virtualenv (XENON_SKIP_VENV=1)"
  else
    _xenon_ensure_venv "$python" "$venv" || return 1
  fi

  bindir="$(_xenon_pick_bindir "$bin_override")"
  echo "Bin dir: ${bindir}"
  local had_bindir=0
  case ":${PATH}:" in
    *":${bindir}:"*) had_bindir=1 ;;
  esac
  command_path="$(_xenon_write_command "$bindir" "$venv")"

  _xenon_ensure_path "$bindir"

  if [[ "$had_bindir" -eq 0 ]]; then
    export PATH="${bindir}:${PATH}"
  fi
  hash -r 2>/dev/null || true

  echo
  echo "Installed: ${command_path}"
  if [[ "$had_bindir" -eq 1 ]] || [[ "$_XENON_SOURCED" -eq 1 ]]; then
    echo "Command available: xenon"
  else
    echo "Command installed. Open a new terminal, or: source ~/.profile"
  fi
  echo "Next: xenon setup"
  XENON_BINDIR="$bindir"
  return 0
}

if [[ "$_XENON_SOURCED" -eq 1 ]]; then
  if _xenon_main "$@"; then
    if [[ -n "${XENON_BINDIR:-}" ]]; then
      case ":${PATH}:" in
        *":${XENON_BINDIR}:"*) ;;
        *) export PATH="${XENON_BINDIR}:${PATH}" ;;
      esac
      hash -r 2>/dev/null || true
    fi
  else
    return 1
  fi
else
  set -euo pipefail
  _xenon_main "$@"
fi
