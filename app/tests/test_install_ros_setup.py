#!/usr/bin/env python3
"""Exercise the installer with fake tools; no ROS or native compilation required."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]


class C20dInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='c20d test ')
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'
        shutil.copytree(PACKAGE / 'scripts', self.app / 'scripts')
        shutil.copytree(PACKAGE / 'config', self.app / 'config')
        shutil.copy2(PACKAGE / 'start.sh', self.app / 'start.sh')
        (self.app / 'ros_ws').mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.setup = self.root / 'ros install' / 'setup.bash'
        self.setup.parent.mkdir()
        self.write_setup('jazzy')
        self.make_executable('uname', '#!/bin/sh\necho Linux\n')
        # Stub dependency check/config rendering; these tests exercise shell
        # environment selection, not ROS or the independently tested renderer.
        self.make_executable('python3', '#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\ntouch "$3"\n')
        self.make_executable('colcon', '''#!/bin/bash
if [[ "$*" == *--help* ]]; then echo '  * status: x'; exit 0; fi
printf '%s\\n%s\\n%s\\n' "$CMAKE_BUILD_PARALLEL_LEVEL" "$MAKEFLAGS" "$*" > "$C20D_TEST_LOG"
mkdir -p install/c20d_driver/lib/c20d_driver
touch install/local_setup.bash
printf '#!/bin/sh\\n' > install/c20d_driver/lib/c20d_driver/c20d_node
chmod +x install/c20d_driver/lib/c20d_driver/c20d_node
''')
        self.make_executable('ros2', '#!/bin/sh\nprintf "ros=%s\\n" "$ROS_DISTRO" > "$C20D_TEST_RUNTIME_LOG"\n')
        self.env = os.environ.copy()
        for key in ('ROS_DISTRO', 'ROS_VERSION', 'AIRBORNE_BUILD_JOBS'):
            self.env.pop(key, None)
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env['PATH'],
                        AIVUDA_C20D_ROS_SETUP=str(self.setup),
                        C20D_TEST_LOG=str(self.root / 'build.log'),
                        C20D_TEST_RUNTIME_LOG=str(self.root / 'runtime.log'))

    def tearDown(self):
        self.temp.cleanup()

    def write_setup(self, distro):
        self.setup.write_text('export ROS_VERSION=2\nexport ROS_DISTRO=' + distro + '\n')

    def make_executable(self, name, content):
        p = self.bin / name
        p.write_text(content)
        p.chmod(0o755)

    def run_script(self, path):
        return subprocess.run(['bash', str(path)], env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def install(self):
        return self.run_script(self.app / 'scripts' / 'pre_install.sh')

    def test_default_four_and_runtime_uses_build_environment(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app / 'ros_build_setup.txt').read_text(), str(self.setup) + '\njazzy\n')
        build = (self.root / 'build.log').read_text().splitlines()
        self.assertEqual(build[:2], ['4', '-j4'])
        self.assertIn('--executor sequential', build[2])
        result = self.run_script(self.app / 'start.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'runtime.log').read_text(), 'ros=jazzy\n')

    def test_custom_job_count_and_invalid_count(self):
        self.env['AIRBORNE_BUILD_JOBS'] = '2'
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'build.log').read_text().splitlines()[:2], ['2', '-j2'])
        self.env['AIRBORNE_BUILD_JOBS'] = '0'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AIRBORNE_BUILD_JOBS', result.stderr)

    def test_runtime_rejects_different_ros_distro_and_setup(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write_setup('humble')
        result = self.run_script(self.app / 'start.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ROS distro mismatch', result.stderr)
        self.env['AIVUDA_C20D_ROS_SETUP'] = str(self.root / 'other.bash')
        result = self.run_script(self.app / 'start.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('The selected ROS setup differs', result.stderr)

    def test_missing_setup_and_non_ros2_are_rejected(self):
        self.env['AIVUDA_C20D_ROS_SETUP'] = str(self.root / 'missing')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ROS setup is unreadable', result.stderr)
        self.env['AIVUDA_C20D_ROS_SETUP'] = str(self.setup)
        self.setup.write_text('export ROS_VERSION=1\nexport ROS_DISTRO=noetic\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Not a valid ROS 2 environment', result.stderr)

    def test_invalid_ros_distro_does_not_fall_back_to_another_install(self):
        self.env.pop('AIVUDA_C20D_ROS_SETUP')
        self.env['ROS_DISTRO'] = '../invalid'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid ROS_DISTRO', result.stderr)

    def test_inherited_different_distro_is_rejected(self):
        self.env['ROS_DISTRO'] = 'humble'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from', result.stderr)


if __name__ == '__main__':
    unittest.main()
