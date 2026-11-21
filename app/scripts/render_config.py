#!/usr/bin/env python3
"""Turn the active Aivuda configuration into ROS parameters and launch settings."""
import math
import sys
from pathlib import Path

import yaml


def render(config_path, output_path):
    config = yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))
    serial = config['serial']
    control = config['control']
    port = serial['port']
    if not isinstance(port, str) or not port.strip():
        raise ValueError('serial.port must be a nonempty path')
    for key in ('enabled', 'teleop_enabled'):
        if type(control[key]) is not bool:
            raise ValueError('control.%s must be boolean' % key)
    numeric = {}
    for key, maximum in [('command_timeout', None), ('max_rate', 80),
                         ('roll_limit', 60), ('pitch_limit', 120)]:
        value = control[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('control.%s must be numeric' % key)
        value = float(value)
        if not math.isfinite(value) or value <= 0 or (maximum is not None and value > maximum):
            raise ValueError('control.%s is outside the supported range' % key)
        numeric[key] = value
    params = {'c20d_driver': {'ros__parameters': {
        'port': port, 'frame_id': 'c20d_head_up', 'update_rate': 100.0,
        'feedback_timeout': 0.5, 'auxiliary_timeout': 0.5,
        'control_enabled': control['enabled'], **numeric}}}
    path = Path(output_path)
    path.write_text(yaml.safe_dump(params, sort_keys=False), encoding='utf-8')
    # A separate launch setting is used because this is not a driver parameter.
    path.with_name('teleop_enabled.txt').write_text(
        'true' if control['teleop_enabled'] else 'false', encoding='utf-8')


if __name__ == '__main__':
    try:
        render(sys.argv[1], sys.argv[2])
    except (IndexError, OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print('Invalid C20D app configuration: %s' % exc, file=sys.stderr)
        sys.exit(1)
