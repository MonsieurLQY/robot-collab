"""No model, server or hardware required to verify termination budgets."""
import json
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from prompting.budget import PlanningBudget, PlanningStopped, deadline
from prompting.dialog_prompter import DialogPrompter
from prompting.robopara.adapter import DesktopTask, SemanticParser
from run_robopara_dialog import _build_parser, run_episode


class PlanningLimitTest(unittest.TestCase):
    def test_call_limit_includes_failed_requests(self):
        budget = PlanningBudget(max_calls=1)
        with self.assertRaises(ValueError):
            with budget.request():
                raise ValueError('API failed')
        with self.assertRaises(PlanningStopped) as raised:
            with budget.request():
                self.fail('over-budget request executed')
        self.assertEqual(raised.exception.reason, 'model_call_limit')
        self.assertEqual(budget.calls, 1)

    def test_blocking_request_timeout_restores_signal(self):
        handler = signal.getsignal(signal.SIGALRM)
        budget = PlanningBudget(request_seconds=0.02)
        with self.assertRaises(PlanningStopped) as raised:
            with budget.request():
                time.sleep(0.2)
        self.assertEqual(raised.exception.reason, 'model_request_timeout')
        self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_episode_deadline_wins_over_request_timeout(self):
        with self.assertRaises(PlanningStopped) as raised:
            with deadline(0.02, 'episode_timeout'):
                with deadline(0.2, 'model_request_timeout'):
                    time.sleep(0.1)
        self.assertEqual(raised.exception.reason, 'episode_timeout')
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_bounded_prompter_does_not_retry_api_errors(self):
        budget = PlanningBudget(max_calls=2)
        prompter = DialogPrompter(DesktopTask({}), SemanticParser(), Mock(),
                                 debug_mode=True, query_budget=budget)
        prompter.debug_mode = False
        prompter.llm_client = Mock()
        prompter.llm_client.generate.side_effect = RuntimeError('network failure')
        with self.assertRaises(RuntimeError):
            prompter.query_once('task', 'agent', max_query=5)
        self.assertEqual(prompter.llm_client.generate.call_count, 1)
        self.assertEqual(budget.calls, 1)

    def test_odd_dialog_limit_cannot_overshoot(self):
        prompter = DialogPrompter(DesktopTask({}), SemanticParser(), Mock(),
            debug_mode=True, max_calls_per_round=3,
            robot_name_map={'left': 'LeftArm', 'right': 'RightArm'})
        prompter.debug_mode = False
        prompter.query_once = Mock(return_value=('PROCEED', {}))
        with tempfile.TemporaryDirectory() as directory:
            prompter.prompt_one_dialog_round({}, [], [], save_path=directory)
        self.assertEqual(prompter.query_once.call_count, 3)

    def test_sdk_retries_disabled_in_bounded_mode(self):
        from prompting.llm_config import VertexGeminiClient
        with patch('prompting.llm_config.genai.Client') as client:
            VertexGeminiClient(credentials=Mock(), request_timeout_s=60)
        options = client.call_args.kwargs['http_options']
        self.assertEqual(options.timeout, 60000)
        self.assertEqual(options.retry_options.attempts, 1)

    def _run(self, directory, outcome, overrides=()):
        import io
        from contextlib import redirect_stdout

        args = _build_parser().parse_args(['--data-dir', directory,
                                          '--run-name', 'bounded', *overrides])
        client = Mock(episode_id='fake')
        frame = Path(directory) / 'latest.jpg'  # Stand-in for the platform frame buffer.
        frame.write_bytes(b'\xff\xd8\xff' + b'jpeg')
        state = {'task': {}, 'state': {}, 'terminal': False, 'task_success': False,
                 'steps': 0, 'image': {'path': str(frame), 'steps': 0, 'sequence': 1}}
        client.call.return_value = state
        prompter = Mock()
        prompter.prompt_one_round.side_effect = outcome
        console = io.StringIO()
        with redirect_stdout(console), \
             patch('run_robopara_dialog.PlannerClient', return_value=client), \
             patch('run_robopara_dialog.DialogPrompter', return_value=prompter):
            result = run_episode(args, 0)
        saved = json.loads((Path(directory) / 'bounded/seed_0/summary.json').read_text())
        terminal = (Path(directory) / 'bounded/seed_0/terminal.txt').read_text()
        self.assertEqual(terminal, console.getvalue())
        self.assertIn('seed=0 task_success=False', terminal)
        self.assertEqual(saved['termination_reason'], result['termination_reason'])
        self.assertEqual(client.call.call_args.args[0], 'close')
        return result, prompter

    def test_two_failed_rounds_stop_and_save_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            result, prompter = self._run(directory, [(False, [], ['invalid'], [])] * 5)
        self.assertEqual(prompter.prompt_one_round.call_count, 2)
        self.assertEqual(result['termination_reason'], 'consecutive_failure_limit')
        self.assertFalse(result['task_success'])

    def test_two_execution_failures_also_stop(self):
        from prompting.robopara.adapter import JointAction
        plan = JointAction([], 'WAIT')
        with tempfile.TemporaryDirectory() as directory:
            result, prompter = self._run(directory, [(True, [plan], [], [])] * 5)
        self.assertEqual(prompter.prompt_one_round.call_count, 2)
        self.assertEqual(result['termination_reason'], 'consecutive_failure_limit')
        self.assertEqual(len(result['rounds']), 2)

    def test_call_limit_is_saved_as_explicit_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            result, prompter = self._run(directory, PlanningStopped('model_call_limit'))
        self.assertEqual(prompter.prompt_one_round.call_count, 1)
        self.assertEqual(result['termination_reason'], 'model_call_limit')

    def test_blocking_planning_stops_and_saves_summary(self):
        def blocking(*args, **kwargs):
            time.sleep(0.3)
            self.fail('deadline failed')
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._run(directory, blocking, ['--max-episode-seconds', '0.05'])
        self.assertEqual(result['termination_reason'], 'episode_timeout')
        self.assertGreater(result['planning_wallclock_s'], 0)

    def test_invalid_time_budgets_rejected(self):
        for value in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                PlanningBudget(max_seconds=value)


if __name__ == '__main__':
    unittest.main()
