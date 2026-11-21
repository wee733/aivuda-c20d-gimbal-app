#!/usr/bin/env bash
set -eo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${AIVUDA_APP_CONFIG_PATH:-$APP_DIR/config/default_config.yaml}"
RUNTIME_DIR="${AIVUDA_APP_RUNTIME_DATA_PATH:-$APP_DIR/runtime}"
source "$APP_DIR/scripts/ros_setup.sh"
BUILD_SETUP_FILE="$APP_DIR/ros_build_setup.txt"
[[ -r "$BUILD_SETUP_FILE" ]] || { echo 'ROS build environment record is missing; reinstall this app.' >&2; exit 1; }
{ IFS= read -r C20D_ROS_SETUP && IFS= read -r C20D_BUILD_DISTRO; } < "$BUILD_SETUP_FILE" || { echo 'ROS build environment record is invalid; reinstall this app.' >&2; exit 1; }
if [[ -n "${AIVUDA_C20D_ROS_SETUP:-}" && "$AIVUDA_C20D_ROS_SETUP" != "${C20D_ROS_SETUP}" ]]; then
  echo 'The selected ROS setup differs from the build environment; reinstall this app.' >&2; exit 1
fi
[[ -r "${C20D_ROS_SETUP}" ]] || { echo "ROS setup used at build time is missing: ${C20D_ROS_SETUP}. Reinstall this app." >&2; exit 1; }
[[ -r "$APP_DIR/ros_ws/install/local_setup.bash" ]] || { echo 'c20d_driver is not built; reinstall this app.' >&2; exit 1; }
mkdir -p "$RUNTIME_DIR"
python3 "$APP_DIR/scripts/render_config.py" "$CONFIG_PATH" "$RUNTIME_DIR/c20d_ros_params.yaml"
c20d_source_ros_setup
[[ "${ROS_DISTRO}" == "${C20D_BUILD_DISTRO}" ]] || { echo "ROS distro mismatch (built with ${C20D_BUILD_DISTRO}, current ${ROS_DISTRO}); reinstall this app." >&2; exit 1; }
source "$APP_DIR/ros_ws/install/local_setup.bash"
exec ros2 launch "$APP_DIR/launch/c20d_app.launch.py" "params_file:=$RUNTIME_DIR/c20d_ros_params.yaml"
