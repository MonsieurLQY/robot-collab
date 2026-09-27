"""Scores reach summary.json and score_report.json without a server or model."""
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from prompting.robopara.adapter import JointAction
import run_robopara_dialog

GOALS = ['marker_in_holder', 'laptop_closed', 'success']
FRAME = b'\xff\xd8\xff' + b'jpeg'


class FakeClient:
    def __init__(self, frame, platform_score=True):
        self.frame, self.platform_score = frame, platform_score
        self.episode_id, self.calls = 'fake', []

    def _state(self, steps, done):
        return {'task': {'goals': GOALS}, 'steps': steps, 'sim_time_s': steps * 0.02,
                'terminal': done, 'task_success': False, 'execution_success': True,
                'state': {'predicates': {'marker_in_holder': done}},
                'image': {'path': str(self.frame), 'steps': steps, 'sequence': 1}}

    def call(self, operation, **payload):
        self.calls.append((operation, payload))
        if operation == 'reset':
            return self._state(0, False)
        if operation == 'execute':
            return self._state(50, True)
        if not self.platform_score:
            raise RuntimeError('server gone')
        return {'score': {'task_id': 'desktop_cleanup', 'seed': 0, 'success': False,
                          'goal_ratio': '1/2', 'goal_results': {'marker_in_holder': True,
                                                                'laptop_closed': False},
                          'total_wallclock_s': 3.0, 'planning_wallclock_s': 1.0,
                          'execution_wallclock_s': 1.5, 'sim_time_s': 1.0,
                          'phase_wallclock_s': {'planning': 1.0, 'execution': 1.5}}}


class RoboparaScoringTest(unittest.TestCase):
    def _main(self, directory, platform_score):
        frame = Path(directory) / 'latest.jpg'
        frame.write_bytes(FRAME)
        client = FakeClient(frame, platform_score)
        prompter = Mock()
        prompter.prompt_one_round.return_value = (True, [JointAction([], 'WAIT')], [], [])
        argv = ['run_robopara_dialog.py', '--data-dir', directory, '--run-name', 'scored']
        with redirect_stdout(io.StringIO()) as console, patch('sys.argv', argv), \
             patch('run_robopara_dialog.PlannerClient', return_value=client), \
             patch('run_robopara_dialog.DialogPrompter', return_value=prompter):
            run_robopara_dialog.main()
        run_dir = Path(directory) / 'scored'
        return (client, console.getvalue(), json.loads((run_dir / 'seed_0/summary.json').read_text()),
                json.loads((run_dir / 'score_report.json').read_text()))

    def test_platform_score_is_saved_and_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            client, console, summary, report = self._main(directory, True)
        execute, close = client.calls[1][1], client.calls[2][1]
        self.assertGreaterEqual(execute['planning_wallclock_s'], 0)
        self.assertEqual(close['planning_wallclock_s'], 0.0)
        self.assertEqual(close['termination_reason'], 'platform_terminal')
        self.assertEqual(summary['score']['goal_ratio'], '1/2')
        self.assertEqual(report['overall']['goal_success_ratio'], '1/2')
        self.assertIn('[score] goals 1/2 (50.0%)', console)
        self.assertIn("seed=0 score: goals 1/2 done=['marker_in_holder']", console)

    def test_client_fallback_when_close_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, summary, report = self._main(directory, False)
        self.assertEqual(summary['score']['extra'], {'source': 'client_fallback'})
        self.assertEqual(summary['score']['goal_results'],
                         {'marker_in_holder': True, 'laptop_closed': False})
        self.assertEqual(report['overall']['goal_success_ratio'], '1/2')
        self.assertIsNotNone(summary['close_error'])


if __name__ == '__main__':
    unittest.main()
