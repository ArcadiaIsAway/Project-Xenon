#!/usr/bin/env bash
# Install the xenon CLI so it is available from any directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv"
BIN_DIR="${XDG_BIN_HOME:-${HOME}/.local/bin}"
PYTHON="${PYTHON:-python3}"

echo "Project: ${ROOT}"
echo "Bin dir: ${BIN_DIR}"

if [[ ! -d "${VENV}" ]]; then
  echo "Creating virtualenv..."
  "${PYTHON}" -m venv "${VENV}"
fi

echo "Installing package (editable)..."
"${VENV}/bin/pip" install -U pip >/dev/null
"${VENV}/bin/pip" install -e "${ROOT}"

mkdir -p "${BIN_DIR}"
ln -sfn "${VENV}/bin/xenon" "${BIN_DIR}/xenon"
chmod +x "${BIN_DIR}/xenon"

# Make ~/.local/bin visible to GUI / compositor sessions (systemd user env).
ENV_DIR="${HOME}/.config/environment.d"
ENV_FILE="${ENV_DIR}/60-xenon-path.conf"
mkdir -p "${ENV_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<EOF
# Added by Project Xenon installer
PATH=${BIN_DIR}:\${PATH}
EOF
  echo "Wrote ${ENV_FILE}"
fi

# Fish
FISH_CONFIG="${HOME}/.config/fish/config.fish"
if [[ -f "${FISH_CONFIG}" ]] && ! grep -q 'fish_add_path.*/.local/bin' "${FISH_CONFIG}" 2>/dev/null; then
  {
    echo ""
    echo "# Project Xenon — ensure ~/.local/bin is on PATH"
    echo "fish_add_path ${BIN_DIR}"
  } >> "${FISH_CONFIG}"
  echo "Updated ${FISH_CONFIG}"
fi

# Bash
BASHRC="${HOME}/.bashrc"
if [[ -f "${BASHRC}" ]] && ! grep -q '\.local/bin' "${BASHRC}" 2>/dev/null; then
  {
    echo ""
    echo "# Project Xenon — ensure ~/.local/bin is on PATH"
    echo "case \":\${PATH}:\" in *\":${BIN_DIR}:\"*) ;; *) export PATH=\"${BIN_DIR}:\${PATH}\" ;; esac"
  } >> "${BASHRC}"
  echo "Updated ${BASHRC}"
fi

echo
echo "Installed: ${BIN_DIR}/xenon"
echo "Try: xenon setup"
echo
echo "If 'xenon' is not found yet:"
echo "  • Fish:  open a new terminal  (or: fish_add_path ${BIN_DIR})"
echo "  • Hyprland: re-login once so environment.d is picked up"
