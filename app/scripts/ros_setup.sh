#!/usr/bin/env bash
# Select a ROS installation at build time. The Aivuda pre-install hook does
# not yet have the app config, so a custom location must come from its service
# environment. Persist the result and use the same installation at runtime.
c20d_resolve_ros_setup() {
  local candidate
  if [[ -n "${AIVUDA_C20D_ROS_SETUP:-}" ]]; then
    C20D_ROS_SETUP="$AIVUDA_C20D_ROS_SETUP"
  elif [[ -n "${ROS_DISTRO:-}" ]]; then
    if [[ ! "${ROS_DISTRO}" =~ ^[a-z][a-z0-9_]*$ ]]; then
      echo "Invalid ROS_DISTRO: ${ROS_DISTRO}" >&2; return 1
    fi
    C20D_ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"
  else
    local candidates=()
    for candidate in /opt/ros/*/setup.bash; do
      [[ -r "$candidate" ]] && candidates+=("$candidate")
    done
    if [[ ${#candidates[@]} -ne 1 ]]; then
      echo 'Cannot select a unique ROS 2 installation. Set AIVUDA_C20D_ROS_SETUP for the AivudaOS service to the absolute setup.bash path, then reinstall.' >&2
      return 1
    fi
    C20D_ROS_SETUP="${candidates[0]}"
  fi
  if [[ "${C20D_ROS_SETUP}" != /* || ! -r "${C20D_ROS_SETUP}" || "${C20D_ROS_SETUP}" == *$'\n'* ]]; then
    echo "ROS setup is unreadable or not an absolute path: ${C20D_ROS_SETUP}. Set AIVUDA_C20D_ROS_SETUP for the AivudaOS service." >&2
    return 1
  fi
}

c20d_source_ros_setup() {
  local inherited_ros_distro="${ROS_DISTRO:-}"
  # shellcheck disable=SC1090
  source "${C20D_ROS_SETUP}"
  if [[ "${ROS_VERSION:-}" != 2 || ! "${ROS_DISTRO:-}" =~ ^[a-z][a-z0-9_]*$ ]]; then
    echo "Not a valid ROS 2 environment: ${C20D_ROS_SETUP} (requires ROS_VERSION=2 and ROS_DISTRO)." >&2
    return 1
  fi
  if [[ -n "$inherited_ros_distro" && "$inherited_ros_distro" != "${ROS_DISTRO}" ]]; then
    echo "Inherited ROS distro ${inherited_ros_distro} differs from ${ROS_DISTRO}; reinstall with a clean service environment." >&2
    return 1
  fi
}
