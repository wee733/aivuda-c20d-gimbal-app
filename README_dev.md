# C20D 开发与测试

独立的 ROS 2 包位于 `app/ros_ws/src/c20d_driver`，包含串口协议、ROS 接口、默认启动文件和测试。AivudaOS 的安装、面板和配置渲染位于 `app/scripts`。驱动使用系统 ROS 构建，独立运行时默认串口为 `/dev/ttyUSB0`，机载安装模板选择 `/dev/ttyTHS1`。

## 构建与回归

在已安装依赖的 Linux ROS 2 环境，从仓库根目录执行以下命令。将第一行替换成要验证的发行版。伪终端集成测试会创建自己的串口设备和 ROS domain。

```bash
source /opt/ros/jazzy/setup.bash
cd app/ros_ws
colcon build --packages-select c20d_driver
source install/setup.bash
AMENT_CPPCHECK_ALLOW_SLOW_VERSIONS=1 colcon test --packages-select c20d_driver
colcon test-result --verbose
cd ../..
ROS_DOMAIN_ID=93 ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s app/tests -v
node --test app/tests/test_ui_control.mjs
./pack.sh
```

`test_ros_pty.py` 分别验证默认 ROS 图和显式启用的接口。它在模拟串口上延迟指令 4 的回应，并在新的相对角目标后重放旧的带戳速度消息。`test_ros_fence_sim.py` 使用独立 ROS 命名空间验证 Web 后端等待服务和处理拒绝。`test_install_ros_setup.py` 检查配置缺少新增字段时继承关闭默认值。

面板读取 AivudaOS 的 `aivuda_ui_locale`。翻译表位于 `app/ui/i18n/locales`。修改控制路径时可切换中英文并检查禁用 Twist relay 后的方向控制。版本清单在 `app/manifest.yaml`，根目录的打包脚本从此清单生成文件名并收录 Git 跟踪的 App 文件。

[协议约定](app/docs/protocol.md)和[验证记录](app/docs/validation.md)包含测过的系统、串口回复条件和历史实机结果。
