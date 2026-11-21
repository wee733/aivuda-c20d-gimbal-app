#!/usr/bin/env bash
set -eo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$APP_DIR/scripts/install_log.sh"
install_log_init 'C20D ROS 2 Gimbal'
install_step 'Check ROS 2 and build tools'
[[ "$(uname -s)" == Linux ]] || { echo 'C20D requires Linux.' >&2; exit 1; }
source "$APP_DIR/scripts/ros_setup.sh"
c20d_resolve_ros_setup
c20d_source_ros_setup
echo "[BUILD] ROS 2 ${ROS_DISTRO}: ${C20D_ROS_SETUP}"
AIRBORNE_BUILD_JOBS="${AIRBORNE_BUILD_JOBS:-4}"
[[ "${AIRBORNE_BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]] || { echo 'AIRBORNE_BUILD_JOBS must be a positive integer.' >&2; exit 1; }
export CMAKE_BUILD_PARALLEL_LEVEL="${AIRBORNE_BUILD_JOBS}" MAKEFLAGS="-j${AIRBORNE_BUILD_JOBS}"
echo "[BUILD] Using ${AIRBORNE_BUILD_JOBS} parallel build jobs."
command -v colcon >/dev/null || { echo 'colcon is required to build c20d_driver.' >&2; exit 1; }
python3 -c 'import yaml' || { echo 'PyYAML is required (python3-yaml).' >&2; exit 1; }
cd "$APP_DIR/ros_ws"
install_step 'Build the C20D ROS 2 driver'
colcon_live_args
colcon build "${COLCON_LIVE_ARGS[@]}" --executor sequential --packages-up-to c20d_driver --cmake-args -DBUILD_TESTING=OFF
install_step 'Check the driver build output'
[[ -r "$APP_DIR/ros_ws/install/local_setup.bash" ]] || { echo 'The build output is missing the ROS workspace setup file.' >&2; exit 1; }
[[ -x "$APP_DIR/ros_ws/install/c20d_driver/lib/c20d_driver/c20d_node" ]] || { echo 'The build output is missing the c20d_node executable.' >&2; exit 1; }
printf '%s\n%s\n' "${C20D_ROS_SETUP}" "${ROS_DISTRO}" > "$APP_DIR/ros_build_setup.txt"
echo '[OK] C20D driver build output is ready.'
install_done
