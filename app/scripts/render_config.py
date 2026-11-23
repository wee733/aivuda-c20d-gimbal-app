#!/usr/bin/env python3
"""Validate and share Aivuda C20D configuration across ROS launch and the WebUI backend."""
import math
import sys
from pathlib import Path

import yaml


def _check_bool(mapping, key, default=None):
    if key not in mapping and default is not None:
        return default
    value = mapping[key]
    if type(value) is not bool:
        raise ValueError(f'{key} must be boolean')
    return value


def _check_number(mapping, key, *, minimum=0.0, exclusive_min=True, maximum=None, default=None):
    if key not in mapping and default is not None:
        return float(default)
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{key} must be numeric')
    value = float(value)
    too_small = value <= minimum if exclusive_min else value < minimum
    if not math.isfinite(value) or too_small or (maximum is not None and value > maximum):
        raise ValueError(f'{key} is outside the supported range')
    return value


def load_app_config(config_path):
    """Load and validate app YAML configuration with upgrade-safe defaults."""
    config = yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('Config root must be a mapping')
    serial = config['serial']
    control = config['control']
    if not isinstance(serial, dict) or not isinstance(control, dict):
        raise ValueError('serial and control sections must be mappings')
    port = serial['port']
    if not isinstance(port, str) or not port.strip():
        raise ValueError('serial.port must be a nonempty path')

    update_rate = _check_number(serial, 'update_rate', minimum=50.0, exclusive_min=False,
                                maximum=150.0, default=100.0)
    feedback_timeout = _check_number(serial, 'feedback_timeout', minimum=0.1, exclusive_min=False,
                                     maximum=10.0, default=0.5)

    try:
        enabled = _check_bool(control, 'enabled')
        teleop_enabled = _check_bool(control, 'teleop_enabled')
        publish_joint_states = _check_bool(control, 'publish_joint_states', default=False)
        enable_auxiliary_interfaces = _check_bool(control, 'enable_auxiliary_interfaces', default=False)
        command_timeout = _check_number(control, 'command_timeout', minimum=0.0, exclusive_min=True, maximum=10.0)
        max_rate = _check_number(control, 'max_rate', minimum=0.0, exclusive_min=True, maximum=80.0)
        roll_limit = _check_number(control, 'roll_limit', minimum=0.0, exclusive_min=True, maximum=60.0)
        pitch_limit = _check_number(control, 'pitch_limit', minimum=0.0, exclusive_min=True, maximum=120.0)
    except ValueError as exc:
        raise ValueError(f'control.{exc}') from None

    return {
        'port': port.strip(),
        'update_rate': update_rate,
        'feedback_timeout': feedback_timeout,
        'enabled': enabled,
        'teleop_enabled': teleop_enabled,
        'publish_joint_states': publish_joint_states,
        'enable_auxiliary_interfaces': enable_auxiliary_interfaces,
        'command_timeout': command_timeout,
        'max_rate': max_rate,
        'roll_limit': roll_limit,
        'pitch_limit': pitch_limit,
    }


def render(config_path, output_path):
    cfg = load_app_config(config_path)
    params = {'c20d_driver': {'ros__parameters': {
        'port': cfg['port'],
        'frame_id': 'c20d_head_up',
        'update_rate': cfg['update_rate'],
        'feedback_timeout': cfg['feedback_timeout'],
        'auxiliary_timeout': 0.5,
        'control_enabled': cfg['enabled'],
        'publish_joint_states': cfg['publish_joint_states'],
        'enable_auxiliary_interfaces': cfg['enable_auxiliary_interfaces'],
        'command_timeout': cfg['command_timeout'],
        'max_rate': cfg['max_rate'],
        'roll_limit': cfg['roll_limit'],
        'pitch_limit': cfg['pitch_limit'],
    }}}
    path = Path(output_path)
    path.write_text(yaml.safe_dump(params, sort_keys=False), encoding='utf-8')
    # A separate launch setting is used because teleop_enabled is not a c20d_driver parameter.
    path.with_name('teleop_enabled.txt').write_text(
        'true' if cfg['teleop_enabled'] else 'false', encoding='utf-8')


if __name__ == '__main__':
    try:
        render(sys.argv[1], sys.argv[2])
    except (IndexError, OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print('Invalid C20D app configuration: %s' % exc, file=sys.stderr)
        sys.exit(1)
