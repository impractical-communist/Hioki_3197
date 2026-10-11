#!/bin/bash
# Grants your user account access to the HIOKI 3197 USB device, so you
# don't need root every time you talk to it.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE_SRC="$SCRIPT_DIR/udev/99-hioki3197.rules"
RULE_DST="/etc/udev/rules.d/99-hioki3197.rules"

echo "Installing udev rule for HIOKI 3197 (vendor 108f, product 3197)..."
sudo cp "$RULE_SRC" "$RULE_DST"

echo "Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger

echo ""
echo "Done. Please UNPLUG and REPLUG the HIOKI 3197's USB cable now"
echo "for the new permissions to take effect."
