# Gimbal Control C20D

This aivudaOS app bundles the C20D ROS 2 serial driver and its browser control panel. The panel supports hold-to-move, stop, center and down-view commands and displays gimbal feedback.

The [C++ driver and messages](app/ros_ws/src/c20d_driver/) are built during installation. The repository also includes the [panel](app/ui/), [control logic](app/scripts/ui_control_logic.py) and [application tests](app/tests/). See the [installation and hardware notes](app/README.md) for serial configuration and ROS setup.

## Build the aivudaOS package

App ID `app_gimbal_control_c20d_20260526_512d09` · Version `ros2-3.0.0`

From a Git checkout, run the following command with Bash, Git, GNU tar, gzip and Python 3 with PyYAML installed.

```bash
./pack.sh
```

The archive in `dist/` has the app manifest at its root. The script uses the working copies of files tracked under `app/`. Stage new application files with Git before packaging. Upload the archive through the aivudaOS app installer.
