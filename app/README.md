# Gimbal Control C20D

AivudaOS app for the C20D ROS 2 serial gimbal driver. Its built-in panel provides hold-to-move controls and live application output. The default driver publishes full feedback and diagnostics and subscribes to relative angle, attitude and angular rate commands.

## Built-in control panel

Start the app in AivudaOS and open its built-in UI. The panel supports switching between **Horizon Rate (`HORI-RATE`)** (hold-to-move angular rate and optional proportional rate), **Horizon Attitude (`HORI-ANGL`)** (LOCK roll/pitch targets), and **FPV Relative (`FPVM-ANGL`)** (relative roll/pitch targets plus FPV sensitivity `-16..15`). **STOP** sends zero angular rate, **Center gimbal** sends one hardware centering request, and **Down view** waits for manual-hold confirmation before sending a one-shot relative-angle target of roll 0 degrees and pitch -90 degrees. Device command buttons expose **Enable motors (2)**, **Disable motors (3)**, **Zero-rate hold (4)**, and **Calibrate gyro (1)** (when IMU temperature is ready). Check the live relative angle, camera IMU attitude/rate, mount orientation, temperature readiness, and command reply badges to confirm device response. Default panel speed is 10 degrees per second, limited to the smaller of 80 degrees per second and the configured `control.max_rate`.

New installations default to `control.max_rate: 80.0`. AivudaOS preserves existing configuration on upgrade: if an earlier installation still has a lower limit such as 28.64788975654116 degrees per second, change `control.max_rate` to 80 in Config and restart the app to use the full 80-degree-per-second range. An intentionally lower configured limit remains effective. Down View also requires `control.pitch_limit` to be at least 90 degrees.

The panel requires an authenticated AivudaOS session and fresh gimbal feedback. All panel movement requires `control.enabled`. The panel uses the driver directly. The language follows the AivudaOS display setting. The server listens on loopback and is exposed through the app's Caddy route. It validates the AivudaOS session, checks request origin and constrains commands. Releasing controls, losing input, leaving the page, or a stale request causes a stop; the driver also retains its independent command timeout. Before Down View, the app calls the manual hold service and waits for the driver to receive the UART acknowledgement. A rejected or timed-out hold cancels the position request. Use only one motion controller at a time.

## Requirements

- Linux, AivudaOS, ROS 2, `colcon`, `python3-yaml` and the ROS dependencies listed in `ros_ws/src/c20d_driver/package.xml`. The installer builds the bundled source and does not install operating-system dependencies.
- The C20D serial interface is 3.3 V TTL, 115200 baud, 8N1, without flow control. Cross TX/RX and connect ground. The AivudaOS user must be able to read and write the device, typically through the `dialout` group.
- Stop `airborne-gimbal.service` and any other application using the same serial port before starting this app. Check the mounting direction and movement clearance before issuing commands.
- The standalone ROS package only requires Linux serial support and ROS 2. See the [validation record](docs/validation.md) for the tested hosts.

The installer uses four parallel build jobs by default. Set the positive integer `AIRBORNE_BUILD_JOBS` in the AivudaOS service environment to change it; this affects building only. Environment variables set in a separate terminal do not automatically reach the running service.

The installer chooses ROS 2 in this order:

1. The absolute `setup.bash` path in the AivudaOS service environment variable `AIVUDA_C20D_ROS_SETUP`.
2. `/opt/ros/<ROS_DISTRO>/setup.bash` when the service has `ROS_DISTRO` set.
3. The sole `/opt/ros/*/setup.bash` installation, if exactly one exists.

If ROS 2 is installed from source elsewhere, or automatic selection is ambiguous, set `AIVUDA_C20D_ROS_SETUP` for the AivudaOS service and reinstall. The app records the setup used for the build and uses it at runtime. Reinstall after changing the ROS installation or distribution.

## Configuration

After installation, set `serial.port` in the AivudaOS configuration page and restart the app. The default `/dev/ttyTHS1` is for the original Jetson. On Raspberry Pi, choose the actual connected device, such as `/dev/serial0` if it exists, and check that the serial console is not using it. For USB adapters, a stable `/dev/serial/by-id/...` path is recommended. Use a different port from the PX4 Agent.

`control.enabled=false` disables control commands while retaining feedback. The Foxglove Twist relay starts only with `control.teleop_enabled=true`. Check this setting on upgrade because AivudaOS can retain the previous explicit value. Set `control.publish_joint_states=true` to publish joint positions in radians. Set `control.enable_auxiliary_interfaces=true` for proportional rate, carrier and camera subscriptions. New installations default all three switches to false. `control.command_timeout` stops a stale speed command after the configured number of seconds (default 0.5). `control.max_rate` is the single-axis speed limit in degrees per second; `control.roll_limit` and `control.pitch_limit` limit target angles in degrees. Software limits do not replace mechanical stops or an emergency stop. Startup sends polling frames but does not automatically enable the motors.

## ROS interfaces

Use one motion controller at a time. Each accepted command replaces the previous two-axis target.

| Direction | Interface | Type and purpose |
| --- | --- | --- |
| Optional relay | `/gimbal_teleop/cmd` | `geometry_msgs/msg/Twist`; `angular.x` and `angular.y` are roll and pitch speed in degrees per second. Positive pitch is up. |
| Input | `/c20d_driver/command/relative_angle` | `c20d_driver/msg/RelativeAngleCommand`; FPV relative angles in degrees. |
| Input | `/c20d_driver/command/attitude` | `c20d_driver/msg/AttitudeCommand`; LOCK attitude angles in degrees. |
| Input | `/c20d_driver/command/angular_rate` | `c20d_driver/msg/AngularRateCommand`; LOCK angular rates in degrees per second. |
| Optional input | `/c20d_driver/command/proportional_rate` | `c20d_driver/msg/ProportionalRateCommand`; requires fresh camera parameters. |
| Output | `/c20d_driver/state` | `c20d_driver/msg/GimbalState` with relative angle, attitude and angular rates from the same serial frame. |
| Output | `/c20d_driver/diagnostics` | Driver health. |
| Optional output | `/c20d_driver/joint_states` | Joint angles in radians. |
| Service | `/c20d_driver/device_command` | `c20d_driver/srv/DeviceCommand`; acceptance alone does not prove execution. |

The Foxglove direction panel should publish `/gimbal_teleop/cmd` continuously and publish zero on release. Its center button stops movement. The driver also stops stale velocity commands; the relay does not cache or resend them.

Use a **separate one-shot command** to center both axes by publishing `c20d_driver/msg/RelativeAngleCommand` to `/c20d_driver/command/relative_angle`:

```json
{"roll_deg": 0.0, "pitch_deg": 0.0, "center_roll": true, "center_pitch": true}
```

Each received `true` toggles the corresponding centering bit; do not publish it continuously. A one-shot `{"roll_deg": 0.0, "pitch_deg": 0.0}` can instead target mechanical relative zero. Read the state topic to check actual angles and device status. Verify axis directions at low speed for a different mounting or firmware.

Foxglove Bridge must run with this app's ROS message workspace (`ros_ws/install/local_setup.bash` in the active app version) and the same ROS domain. Allow clients to publish the control topics they use. Sourcing a separate terminal does not change an already running Bridge; restart it and reconnect Foxglove after updating. See `DRIVER_README.md` for protocol details and limits.

## Source

The bundled driver comes from `wee733/C20C_Ros2Control`, commit `72e95026598181a3108683576e9de908f977a900`, upstream driver version 0.3.4. The bundled 0.4.0 adds optional interfaces and an acknowledged manual hold. See `DRIVER_LICENSE` and `DRIVER_README.md` for the upstream license and documentation.
