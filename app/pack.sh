#!/usr/bin/env bash
set -eo pipefail
export COPYFILE_DISABLE=1
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:-$(dirname "$APP_DIR")/dist}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
tar --no-xattrs --no-acls --exclude='._*' --exclude='*/._*' --exclude='.DS_Store' --exclude='./ros_ws/build' --exclude='./ros_ws/install' --exclude='./ros_ws/log' --exclude='./runtime' --exclude='__pycache__' -czf "$OUT_DIR/gimbal-control-c20d-ros2-1.0.0.tar.gz" -C "$APP_DIR" .
echo "Created $OUT_DIR/gimbal-control-c20d-ros2-1.0.0.tar.gz"
