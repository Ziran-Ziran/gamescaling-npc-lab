"""Machine settings must be resolved before starting an inference process."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from controller import backends, evaluate_saved
from controller.wsl_bridge import WSLBridge


PROFILES = [
    {'GAMESCALING_WSL_DISTRO': 'Ubuntu',
     'GAMESCALING_WSL_CLIENT_REPO': '/home/alice/GameScaling',
     'GAMESCALING_WSL_PYTHON': '/home/alice/venv/bin/python'},
    {'GAMESCALING_WSL_DISTRO': 'Debian',
     'GAMESCALING_WSL_CLIENT_REPO': '/srv/team space/GameScaling',
     'GAMESCALING_WSL_PYTHON': '/opt/game env/bin/python'},
]


class MachineConfigurationTests(unittest.TestCase):
    def bridge_command(self, env, **kwargs):
        process = MagicMock()
        process.stdout.readline.return_value = b''
        process.poll.return_value = 0
        with patch.dict(os.environ, env, clear=True), patch(
            'controller.wsl_bridge.subprocess.Popen', return_value=process
        ) as launch, patch.object(WSLBridge, '_receive', return_value={'type': 'ready'}):
            with WSLBridge(worker_path=r'D:\team space\policy_worker.py', **kwargs):
                pass
        return launch.call_args.args[0]

    def test_two_machines_use_their_own_distribution_and_paths(self):
        for env in PROFILES:
            with self.subTest(distro=env['GAMESCALING_WSL_DISTRO']):
                self.assertEqual(self.bridge_command(env), [
                    'wsl.exe', '-d', env['GAMESCALING_WSL_DISTRO'], '--',
                    env['GAMESCALING_WSL_PYTHON'], '/mnt/d/team space/policy_worker.py',
                    '--repo', env['GAMESCALING_WSL_CLIENT_REPO'],
                ])

    def test_explicit_arguments_override_environment(self):
        command = self.bridge_command(PROFILES[0], distro='Custom',
                                      repo='/work/client', python='/work/env/bin/python')
        self.assertEqual(command[2], 'Custom')
        self.assertEqual(command[4], '/work/env/bin/python')
        self.assertEqual(command[-1], '/work/client')

    def test_missing_or_blank_settings_fail_before_process_launch(self):
        for name in PROFILES[0]:
            for invalid in (None, '', '   '):
                env = PROFILES[0].copy()
                if invalid is None:
                    del env[name]
                else:
                    env[name] = invalid
                with self.subTest(name=name, value=invalid), patch.dict(
                    os.environ, env, clear=True
                ), patch('controller.wsl_bridge.subprocess.Popen') as launch, patch.object(
                    WSLBridge, '_receive', return_value={'type': 'ready'}
                ):
                    with self.assertRaisesRegex(ValueError, name):
                        with WSLBridge():
                            pass
                    launch.assert_not_called()

    def test_wsl_paths_must_be_absolute_linux_paths(self):
        for name in ('GAMESCALING_WSL_CLIENT_REPO', 'GAMESCALING_WSL_PYTHON'):
            for value in ('relative/path', r'C:\wrong-platform\path'):
                env = {**PROFILES[0], name: value}
                with self.subTest(name=name, value=value), patch.dict(
                    os.environ, env, clear=True
                ), patch('controller.wsl_bridge.subprocess.Popen') as launch, patch.object(
                    WSLBridge, '_receive', return_value={'type': 'ready'}
                ):
                    with self.assertRaisesRegex(ValueError, name):
                        with WSLBridge():
                            pass
                    launch.assert_not_called()

    def test_invalid_explicit_setting_does_not_fall_back_to_environment(self):
        with patch.dict(os.environ, PROFILES[0], clear=True), patch(
            'controller.wsl_bridge.subprocess.Popen'
        ) as launch:
            with self.assertRaisesRegex(ValueError, 'GAMESCALING_WSL_PYTHON'):
                WSLBridge(python='')
            launch.assert_not_called()

    def test_distribution_controls_are_rejected_without_echoing_the_value(self):
        for value in ('--shutdown', 'Ubuntu\nprivate-value'):
            with self.subTest(value=value), patch.dict(
                os.environ, {**PROFILES[0], 'GAMESCALING_WSL_DISTRO': value}, clear=True
            ), patch('controller.wsl_bridge.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'GAMESCALING_WSL_DISTRO') as error:
                    WSLBridge()
                self.assertNotIn(value, str(error.exception))
                launch.assert_not_called()

    def test_worker_cleanup_targets_the_configured_distribution(self):
        process = MagicMock()
        process.stdout.readline.return_value = b''
        process.poll.return_value = 0
        with patch.dict(os.environ, PROFILES[1], clear=True), patch(
            'controller.wsl_bridge.subprocess.Popen', return_value=process
        ), patch('controller.wsl_bridge.subprocess.run') as cleanup, patch.object(
            WSLBridge, '_receive', return_value={'type': 'ready', 'pid': 100, 'start_time': '123'}
        ):
            with WSLBridge(worker_path=r'D:\worker.py'):
                pass
        self.assertEqual(cleanup.call_args.args[0][:4], ['wsl.exe', '-d', 'Debian', '--'])

    def test_cloud_uses_windows_configuration_without_wsl_settings(self):
        for repo in (r'D:\projects\GameScaling', r'\\wsl.localhost\Debian\srv\GameScaling'):
            with self.subTest(repo=repo), patch.dict(
                os.environ, {'GAMESCALING_WINDOWS_CLIENT_REPO': repo}, clear=True
            ), patch.object(backends, 'WSLBridge') as bridge:
                backends.create_bridge('cloud')
                command = bridge.call_args.kwargs['command']
                self.assertEqual(command[command.index('--repo') + 1], repo)
                self.assertEqual(command[-1], 'http://127.0.0.1:18080/v1')

    def test_cloud_missing_or_relative_repository_does_not_launch(self):
        for value in ('', '   ', r'relative\client', '/linux/path'):
            with self.subTest(value=value), patch.dict(
                os.environ, {'GAMESCALING_WINDOWS_CLIENT_REPO': value}, clear=True
            ), patch.object(backends, 'WSLBridge') as bridge:
                with self.assertRaisesRegex(ValueError, 'GAMESCALING_WINDOWS_CLIENT_REPO'):
                    backends.create_bridge('cloud')
                bridge.assert_not_called()


class JsonLineIntegrationTests(unittest.TestCase):
    def test_custom_worker_roundtrip_requires_real_newlines_without_wsl(self):
        worker = (
            "import json,sys\n"
            "print(json.dumps({'type':'ready'}),flush=True)\n"
            "for line in sys.stdin:\n"
            " q=json.loads(line)\n"
            " print(json.dumps({'id':q['id'],'result':q['value']}),flush=True)\n"
        )
        with patch.dict(os.environ, {name: '' for name in PROFILES[0]}):
            with WSLBridge(command=[sys.executable, '-u', '-c', worker]) as bridge:
                for value in ('first', '第二条请求'):
                    self.assertEqual(bridge.request({'value': value}, timeout=3)['result'], value)


class EvaluationConfigurationTests(unittest.TestCase):
    def test_environment_default_and_cli_override_use_native_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            default = str(Path(folder) / 'default client')
            override = str(Path(folder) / 'other client')
            with patch.dict(os.environ, {'GAMESCALING_CLIENT_REPO': default}, clear=True):
                self.assertEqual(evaluate_saved.parse_args(['--controls']).official_repo, default)
                args = evaluate_saved.parse_args(['--doom-defaults', '--official-repo', override])
                self.assertTrue(args.controls)
                self.assertEqual(args.official_repo, override)

    def test_non_control_evaluation_does_not_require_a_repository(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(evaluate_saved.parse_args([]).official_repo)

    def invoke(self, argv, env):
        fake_policy = ModuleType('gamescaling.policy')
        for name in ('PolicyConfig', 'encode_image', 'finalize'):
            setattr(fake_policy, name, MagicMock())
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, {'gamescaling': ModuleType('gamescaling'), 'gamescaling.policy': fake_policy}
        ), patch('sys.argv', ['evaluate_saved', *argv]), patch('requests.Session') as session:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                try:
                    evaluate_saved.main()
                except SystemExit as error:
                    self.assertEqual(error.code, 2)
                except Exception as error:
                    self.fail(f'Expected configuration error before external work, got {type(error).__name__}')
                else:
                    self.fail('Expected missing client repository error')
            session.assert_not_called()
            return errors.getvalue()

    def test_controls_missing_repository_fails_before_network_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'results'
            errors = self.invoke(['--controls', '--output', str(output)], {})
            self.assertIn('GAMESCALING_CLIENT_REPO', errors)
            self.assertFalse(output.exists())

    def test_doom_defaults_also_requires_repository_configuration(self):
        errors = self.invoke(['--doom-defaults'], {})
        self.assertIn('GAMESCALING_CLIENT_REPO', errors)
