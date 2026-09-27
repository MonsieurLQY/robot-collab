"""Each round reads the platform's latest frame; no model, server or simulator needed."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompting.robopara.adapter import DesktopTask, JointAction
from run_robopara_dialog import _build_parser, resolve_visual_input, run_episode

JPEG = b'\xff\xd8\xff'


class FakePlatform:
    """Planner server stand-in whose frame buffer is overwritten on every execute."""

    def __init__(self, buffer_path, *, rounds=2, stale_after_execute=False, image=True):
        self.buffer_path = buffer_path
        self.rounds = rounds
        self.stale_after_execute = stale_after_execute
        self.image = image
        self.steps = 0
        self.executes = 0
        self.episode_id = 'fake'

    def _response(self, frame_steps=None):
        result = {'task': {}, 'state': {'steps': self.steps}, 'steps': self.steps,
                  'terminal': self.executes >= self.rounds, 'task_success': self.executes >= self.rounds,
                  'execution_success': True}
        if self.image:
            result['image'] = {'path': str(self.buffer_path), 'sequence': self.executes + 1,
                               'round_id': self.executes,
                               'steps': self.steps if frame_steps is None else frame_steps}
        return result

    def call(self, operation, **payload):
        if operation == 'reset':
            self.buffer_path.write_bytes(JPEG + b'frame-after-reset')
            return self._response()
        if operation == 'execute':
            previous = self.steps
            self.executes += 1
            self.steps += 278
            if self.stale_after_execute:
                return self._response(frame_steps=previous)  # Round-end capture failed.
            self.buffer_path.write_bytes(JPEG + f'frame-after-execute-{self.executes}'.encode())
            return self._response()
        return {}


class LatestFrameInputTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.buffer_path = self.directory / 'buffer' / 'latest.jpg'
        self.buffer_path.parent.mkdir()
        self.seen = []  # Image bytes the model would receive, read at call time.

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, platform, extra_args=()):
        args = _build_parser().parse_args(
            ['--data-dir', str(self.directory), '--run-name', 'live', *extra_args])

        def prompt_one_round(state, save_path, image_paths):
            self.seen.append({'paths': list(image_paths),
                              'bytes': [Path(path).read_bytes() for path in image_paths]})
            return True, [JointAction([], 'WAIT')], [], []

        prompter = Mock()
        prompter.prompt_one_round.side_effect = prompt_one_round
        with redirect_stdout(io.StringIO()), \
             patch('run_robopara_dialog.PlannerClient', return_value=platform), \
             patch('run_robopara_dialog.DialogPrompter', return_value=prompter):
            summary = run_episode(args, 0)
        return summary, self.directory / 'live' / 'seed_0'

    def test_first_round_reads_reset_frame_and_later_rounds_read_newest(self):
        summary, run_dir = self._run(FakePlatform(self.buffer_path))

        self.assertEqual([item['bytes'] for item in self.seen], [
            [JPEG + b'frame-after-reset'],
            [JPEG + b'frame-after-execute-1'],
        ])
        # The model reads a per-round copy, so the log keeps exactly what it saw
        # even after the platform buffer is overwritten.
        for index, item in enumerate(self.seen):
            self.assertEqual(item['paths'], [run_dir / f'round_{index}' / 'scene.jpg'])
            self.assertEqual(item['paths'][0].read_bytes(), item['bytes'][0])
        self.assertEqual(summary['observation_mode'], 'latest_frame')
        self.assertEqual(summary['termination_reason'], 'task_success')
        self.assertEqual([record['image']['steps'] for record in summary['rounds']], [0, 278])
        request = json.loads((run_dir / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['image_paths'], [str(self.buffer_path)])
        self.assertEqual(request['observation_mode'], 'latest_frame')

    def test_missing_frame_stops_with_clear_error(self):
        summary, _ = self._run(FakePlatform(self.buffer_path, image=False))
        self.assertEqual(summary['termination_reason'], 'run_error')
        self.assertIn('--no-frame-buffer', summary['error'])
        self.assertEqual(self.seen, [])

    def test_stale_frame_is_never_sent_to_the_model(self):
        summary, _ = self._run(FakePlatform(self.buffer_path, stale_after_execute=True))
        self.assertEqual(summary['termination_reason'], 'run_error')
        self.assertIn('stale', summary['error'])
        self.assertEqual(len(self.seen), 1)  # Only the valid reset frame was used.

    def test_explicit_static_image_skips_the_buffer(self):
        static = self.directory / 'scene.png'
        static.write_bytes(b'\x89PNG\r\n\x1a\nstatic')
        summary, run_dir = self._run(FakePlatform(self.buffer_path), ['--image', str(static)])
        self.assertEqual([item['paths'] for item in self.seen], [[static], [static]])
        self.assertEqual(summary['observation_mode'], 'initial_image')
        self.assertFalse(list(run_dir.glob('round_*/scene.*')))

    def test_default_no_longer_reads_office_image(self):
        args = SimpleNamespace(image=None, instruction='clean desk', instruction_file=None)
        self.assertEqual(resolve_visual_input(args), ([], 'clean desk'))

    def test_live_prompt_says_image_is_current(self):
        task = DesktopTask({'instruction': 'Close laptop'}, image_input=True, live_image=True)
        prompt = task.get_agent_prompt({'secret_position': [1, 2]}, 'LeftArm')
        self.assertIn('current scene', prompt)
        self.assertNotIn('initial scene', prompt)
        self.assertNotIn('secret_position', prompt)


if __name__ == '__main__':
    unittest.main()
