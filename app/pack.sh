#!/usr/bin/env bash
set -eo pipefail
export COPYFILE_DISABLE=1
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:-$(dirname "$APP_DIR")/dist}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
archive=$(python3 - "$APP_DIR/manifest.yaml" <<'PYTHON'
import re
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = yaml.safe_load(manifest_file)
parts = [manifest["app_id"], manifest["version"]]
if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", part) for part in parts):
    raise SystemExit("Invalid app ID or version")
print("-".join(parts) + ".tar.gz")
PYTHON
)
tar --no-xattrs --no-acls --exclude='._*' --exclude='*/._*' --exclude='.DS_Store' --exclude='./ros_ws/build' --exclude='./ros_ws/install' --exclude='./ros_ws/log' --exclude='./runtime' --exclude='./ros_build_setup.txt' --exclude='__pycache__' -czf "$OUT_DIR/$archive" -C "$APP_DIR" .
echo "Created $OUT_DIR/$archive"
