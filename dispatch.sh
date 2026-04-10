#!/usr/bin/env bash
# dispatch.sh
# Assumes all fix files live next to this script.
# Run from the directory containing this script:
#   cd /path/to/fix-files && bash dispatch.sh /path/to/neverdue
#
# Usage: bash dispatch.sh <neverdue_root>
#
# Files handled:
#   billing_models.py              -> billing/models.py          (full replace)
#   billing_views_webhook.py       -> billing/views/webhook.py   (full replace)

set -euo pipefail

ROOT="${1:-}"
if [[ -z "$ROOT" ]]; then
  echo "Usage: bash dispatch.sh <neverdue_root>" >&2
  exit 1
fi

if [[ ! -d "$ROOT" ]]; then
  echo "Error: '$ROOT' is not a directory." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

replace() {
  local src="$SCRIPT_DIR/$1"
  local dst="$ROOT/$2"
  if [[ ! -f "$src" ]]; then
    echo "MISSING fix file: $src" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "OK  $2"
}

echo "=== dispatch: applying fixes to $ROOT ==="

# ── Full replacements ──────────────────────────────────────────────────────────

replace billing_models.py           billing/models.py
replace billing_views_webhook.py    billing/views/webhook.py

echo "=== done ==="
