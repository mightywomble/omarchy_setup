#!/usr/bin/env bash
# Launches the Omarchy setup wizard (GTK4 + libadwaita).
# Called by apply.sh when run with no arguments in a graphical session.
#
# Usage: run.sh [wizard args...]
#   --print-json  Emit a default config JSON to stdout without a window
#                 (for headless/SSH validation and scripting).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SETUP_DIR="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found — cannot launch the wizard." >&2
  echo "Run apply.sh --cli for the interactive terminal flow instead." >&2
  exit 1
fi

exec python3 "$SCRIPT_DIR/wizard.py" "$SETUP_DIR" "$@"
