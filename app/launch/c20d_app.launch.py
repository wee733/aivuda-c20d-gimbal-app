from pathlib import Path
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, ExecuteProcess, OpaqueFunction, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def nodes(context):
    params_file = LaunchConfiguration('params_file').perform(context)
    enabled = Path(params_file).with_name('teleop_enabled.txt').read_text().strip() == 'true'
    driver = Node(package='c20d_driver', executable='c20d_node',
                  name='c20d_driver', output='screen', parameters=[params_file])
    result = [driver, RegisterEventHandler(OnProcessExit(
        target_action=driver, on_exit=[EmitEvent(event=Shutdown(reason='C20D driver exited'))]))]
    panel = ExecuteProcess(
        cmd=[sys.executable, str(Path(__file__).resolve().parent.parent / 'scripts' / 'ui_control_server.py')],
        name='c20d_control_panel', output='screen')
    result.extend([panel, RegisterEventHandler(OnProcessExit(
        target_action=panel, on_exit=[EmitEvent(event=Shutdown(reason='C20D control panel exited'))]))])
    if enabled:
        relay = Node(package='c20d_driver', executable='gimbal_teleop_relay.py',
                     name='gimbal_teleop_relay', output='screen')
        result.extend([relay, RegisterEventHandler(OnProcessExit(
            target_action=relay, on_exit=[EmitEvent(event=Shutdown(reason='C20D relay exited'))]))])
    return result


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('params_file'), OpaqueFunction(function=nodes)])
