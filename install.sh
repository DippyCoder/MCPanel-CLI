#!/bin/bash
# MCPanel CLI installer.
#
#   ./install.sh            → symlink bin/mcpanel into ~/.local/bin (no build)
#   ./install.sh --pip      → pip install --user (proper console-script entry)
#   ./install.sh --uninstall
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo ""
echo "  MCPanel CLI installer"
echo "  ====================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 is not installed."
    exit 1
fi

if [ "$1" = "--uninstall" ]; then
    rm -f "${BIN_DIR}/mcpanel"
    pip uninstall -y mcpanel-cli 2>/dev/null || true
    echo "[done] Removed mcpanel from ${BIN_DIR} and pip."
    exit 0
fi

if [ "$1" = "--pip" ]; then
    echo "[1/2] Installing with pip (--user)..."
    python3 -m pip install --user --upgrade "${REPO}"
    echo ""
    echo "[2/2] Installed. The 'mcpanel' command is now on your PATH (via pip)."
    echo "      If not found, add ~/.local/bin to PATH."
    exit 0
fi

echo "[1/2] Linking launcher into ${BIN_DIR}..."
mkdir -p "${BIN_DIR}"
chmod +x "${REPO}/bin/mcpanel"
ln -sf "${REPO}/bin/mcpanel" "${BIN_DIR}/mcpanel"

echo "[2/2] Done."
echo ""
case ":${PATH}:" in
    *":${BIN_DIR}:"*) echo "  ${BIN_DIR} is on your PATH — run:  mcpanel --help" ;;
    *) echo "  Add ${BIN_DIR} to your PATH, then run:  mcpanel --help"
       echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac
echo ""
