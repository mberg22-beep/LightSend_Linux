#!/usr/bin/env bash
# Adds LightSend to the applications menu for the current user.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$apps" "$icons"

python3 - "$here/lightsend/assets/LightSend.ico" "$icons/lightsend.png" <<'EOF'
import sys
from PIL import Image
Image.open(sys.argv[1]).convert("RGBA").save(sys.argv[2])
EOF

sed "s|@DIR@|$here|" "$here/packaging/lightsend.desktop.in" > "$apps/lightsend.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" || true
echo "Installed $apps/lightsend.desktop"
