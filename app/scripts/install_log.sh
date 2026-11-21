#!/usr/bin/env bash
# Human-readable milestones for the AivudaOS installer; preserve command output and exit status.
INSTALL_STAGE='preparing installation'
install_log_init() {
  # AivudaOS receives PTY output as text, not an interactive terminal.
  # Keep diagnostics and exit codes while avoiding GCC OSC-8 hyperlinks and colors.
  export GCC_URLS=no GCC_COLORS='' NO_COLOR=1 CLICOLOR=0 COLCON_COLOR_OUTPUT=0
  INSTALL_APP="$1"
  echo "[INSTALL] Preparing $INSTALL_APP"
  trap 'install_log_on_exit $?' EXIT
}
install_step() {
  INSTALL_STAGE="$1"
  echo "[STEP] $INSTALL_STAGE"
}
install_log_on_exit() {
  local code="$1"
  if (( code != 0 )); then
    printf '[ERROR] %s: %s (exit code %s). See the output above.\n' "$INSTALL_APP" "$INSTALL_STAGE" "$code" >&2
  fi
}
install_done() {
  echo "[DONE] $INSTALL_APP preparation finished. AivudaOS will complete installation."
}
colcon_live_args() {
  COLCON_LIVE_ARGS=(--event-handlers console_direct+)
  # status is an optional colcon-notification plugin. Disable its 10 Hz
  # terminal redraw only when the target installation actually provides it.
  local help_text
  help_text="$(colcon build --help 2>/dev/null)" || { echo '[ERROR] Cannot read colcon help. Check the colcon installation.' >&2; return 1; }
  if printf '%s\n' "$help_text" | grep -E '^[[:space:]]*[*][[:space:]]+status:' >/dev/null; then
    COLCON_LIVE_ARGS+=(status-)
  fi
  # console_direct already emits stderr; avoid repeating it after a job fails.
  if printf '%s\n' "$help_text" | grep -E '^[[:space:]]*[*][[:space:]]+console_stderr:' >/dev/null; then
    COLCON_LIVE_ARGS+=(console_stderr-)
  fi
  echo '[BUILD] Live build output follows.'
}
