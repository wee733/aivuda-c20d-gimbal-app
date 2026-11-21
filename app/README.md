# Gimbal Control C20D

AivudaOS app for the C20D ROS 2 serial gimbal driver. Its built-in panel provides hold-to-move controls and live application output. The Foxglove Twist control relay remains available.

## Built-in control panel

Start the app in AivudaOS and open its built-in UI. Hold a direction button to move; release it to stop. The separate **STOP** button sends zero angular rate, **Center gimbal** sends one centering request, and **Down View** sends a one-shot relative-angle target of roll 0 degrees and pitch -90 degrees. Check the feedback values to confirm device response. Default panel speed is 10 degrees per second, limited to the smaller of 80 degrees per second and the configured `control.max_rate`.

New installations default to `control.max_rate: 80.0`. AivudaOS preserves existing configuration on upgrade: if an earlier installation still has a lower limit such as 28.64788975654116 degrees per second, change `control.max_rate` to 80 in Config and restart the app to use the full 80-degree-per-second range. An intentionally lower configured limit remains effective. Down View also requires `control.pitch_limit` to be at least 90 degrees.

The panel requires an authenticated AivudaOS session and fresh gimbal feedback. Direction control is unavailable when `control.enabled` or `control.teleop_enabled` is false; centering and Down View require `control.enabled`. The server listens on loopback and is exposed through the app's Caddy route. It validates the AivudaOS session, checks request origin and constrains commands. Releasing controls, losing input, leaving the page, or a stale request causes a stop; the driver also retains its independent command timeout. Before Down View, the app waits for the driver to echo a zero-rate command; if this confirmation times out it does not send the position target. Use only one motion controller at a time.

## Requirements

- Linux, AivudaOS, ROS 2, `colcon`, `python3-yaml` and the ROS dependencies listed in `ros_ws/src/c20d_driver/package.xml`. The installer builds the bundled source and does not install operating-system dependencies.
- The C20D serial interface is 3.3 V TTL, 115200 baud, 8N1, without flow control. Cross TX/RX and connect ground. The AivudaOS user must be able to read and write the device, typically through the `dialout` group.
- Stop `airborne-gimbal.service` and any other application using the same serial port before starting this app. Check the mounting direction and movement clearance before issuing commands.
- An earlier app version was tested on Ubuntu 24.04 / ROS 2 Jazzy on the original Jetson. Raspberry Pi / Debian and other ROS 2 distributions have not been tested on target hardware.

The installer uses four parallel build jobs by default. Set the positive integer `AIRBORNE_BUILD_JOBS` in the AivudaOS service environment to change it; this affects building only. Environment variables set in a separate terminal do not automatically reach the running service.

The installer chooses ROS 2 in this order:

1. The absolute `setup.bash` path in the AivudaOS service environment variable `AIVUDA_C20D_ROS_SETUP`.
2. `/opt/ros/<ROS_DISTRO>/setup.bash` when the service has `ROS_DISTRO` set.
3. The sole `/opt/ros/*/setup.bash` installation, if exactly one exists.

If ROS 2 is installed from source elsewhere, or automatic selection is ambiguous, set `AIVUDA_C20D_ROS_SETUP` for the AivudaOS service and reinstall. The app records the setup used for the build and uses it at runtime. Reinstall after changing the ROS installation or distribution.

## Configuration

After installation, set `serial.port` in the AivudaOS configuration page and restart the app. The default `/dev/ttyTHS1` is for the original Jetson. On Raspberry Pi, choose the actual connected device, such as `/dev/serial0` if it exists, and check that the serial console is not using it. For USB adapters, a stable `/dev/serial/by-id/...` path is recommended. Use a different port from the PX4 Agent.

`control.enabled=false` disables control commands while retaining feedback. `control.teleop_enabled=false` disables the Foxglove Twist relay. `control.command_timeout` stops a stale speed command after the configured number of seconds (default 0.5). `control.max_rate` is the single-axis speed limit in degrees per second; `control.roll_limit` and `control.pitch_limit` limit target angles in degrees. Software limits do not replace mechanical stops or an emergency stop. Startup sends polling frames but does not automatically enable the motors.

## ROS interfaces

Use one motion controller at a time. Each accepted command replaces the previous two-axis target.

| Direction | Interface | Type and purpose |
| --- | --- | --- |
| Input | `/gimbal_teleop/cmd` | `geometry_msgs/msg/Twist`; `angular.x` and `angular.y` are roll and pitch speed in degrees per second. Positive pitch is up. |
| Input | `/c20d_driver/command/relative_angle` | `c20d_driver/msg/RelativeAngleCommand`; FPV relative angles in degrees. |
| Input | `/c20d_driver/command/attitude` | `c20d_driver/msg/AttitudeCommand`; LOCK attitude angles in degrees. |
| Input | `/c20d_driver/command/angular_rate` | `c20d_driver/msg/AngularRateCommand`; LOCK angular rates in degrees per second. |
| Input | `/c20d_driver/command/proportional_rate` | `c20d_driver/msg/ProportionalRateCommand`; requires fresh camera parameters. |
| Output | `/c20d_driver/state`, `state/relative_angle`, `state/attitude`, `state/angular_rate` | Gimbal feedback and state. |
| Output | `/c20d_driver/joint_states`, `/c20d_driver/diagnostics` | Joint angles and diagnostics. |
| Service | `/c20d_driver/device_command` | `c20d_driver/srv/DeviceCommand`; acceptance alone does not prove execution. |

The Foxglove direction panel should publish `/gimbal_teleop/cmd` continuously and publish zero on release. Its center button stops movement. The driver also stops stale velocity commands; the relay does not cache or resend them.

Use a **separate one-shot command** to center both axes by publishing `c20d_driver/msg/RelativeAngleCommand` to `/c20d_driver/command/relative_angle`:

```json
{"roll_deg": 0.0, "pitch_deg": 0.0, "center_roll": true, "center_pitch": true}
```

Each received `true` toggles the corresponding centering bit; do not publish it continuously. A one-shot `{"roll_deg": 0.0, "pitch_deg": 0.0}` can instead target mechanical relative zero. Command echoes only indicate acceptance by the driver; check feedback and device status. Verify axis directions at low speed for a different mounting or firmware.

Foxglove Bridge must run with this app's ROS message workspace (`ros_ws/install/local_setup.bash` in the active app version) and the same ROS domain. Allow clients to publish control topics, and broadcast `/c20d_driver/command_echo/*` and `/gimbal_teleop/input_echo` for type discovery. Sourcing a separate terminal does not change an already running Bridge; restart it and reconnect Foxglove after updating. See `DRIVER_README.md` for protocol details and limits.

## Source

The bundled driver comes from `wee733/C20C_Ros2Control`, commit `72e95026598181a3108683576e9de908f977a900`, driver version 0.3.4. See `DRIVER_LICENSE` and `DRIVER_README.md` for the upstream license and documentation.
