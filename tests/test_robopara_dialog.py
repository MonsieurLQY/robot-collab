"""Exercise the unchanged dialog/replanning loop with deterministic model replies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from prompting.dialog_prompter import DialogPrompter
from prompting.robopara.adapter import DesktopTask, PlatformFeedback, SemanticParser


class DialogIntegrationTest(unittest.TestCase):
    def test_feedback_replans_and_preserves_both_speakers(self):
        client = Mock()
        client.call.side_effect = [
            {"valid": False, "errors": ["right is not holding marker"]},
            {"valid": True, "errors": []},
        ]
        task = DesktopTask({"instruction": "clean desk"})
        prompter = DialogPrompter(task, SemanticParser(), PlatformFeedback(client),
            debug_mode=True, robot_name_map={"left": "LeftArm", "right": "RightArm"},
            num_replans=2, max_calls_per_round=2)
        # Debug bypasses multi-agent sampling, so disable it after credential-free setup.
        prompter.debug_mode = False
        invalid = 'EXECUTE\nNAME LeftArm ACTION WAIT\nNAME RightArm ACTION PLACE marker pen_holder'
        valid = 'EXECUTE\nNAME LeftArm ACTION WAIT\nNAME RightArm ACTION PICK marker desktop_surface'
        prompter.query_once = Mock(side_effect=[('Discuss ownership first.', {}), (invalid, {}), (valid, {})])
        with tempfile.TemporaryDirectory() as directory:
            ready, plans, feedback, chat = prompter.prompt_one_round({}, directory)
            self.assertTrue(ready)
            self.assertEqual(plans[0].actions[1]['skill'], 'pick')
            self.assertIn('right is not holding marker', prompter.query_once.call_args.args[0])
            self.assertTrue(any('[RightArm]' in text for text in chat))
            prompter.post_execute_update('{}', True, plans[0].get_action_desp())
            self.assertIn('PICK marker', prompter.get_round_history())

    def test_no_feedback_does_not_query_validation(self):
        client = Mock()
        self.assertEqual(PlatformFeedback(client, enabled=False).give_feedback(None), (True, ''))
        client.call.assert_not_called()


class VisualInputTest(unittest.TestCase):
    def test_multimodal_bytes_reach_sdk(self):
        from types import SimpleNamespace
        from prompting.llm_config import VertexGeminiClient
        client = object.__new__(VertexGeminiClient)
        client.model = 'test-model'
        client.client = Mock()
        client.client.models.generate_content.return_value = SimpleNamespace(
            text='EXECUTE', usage_metadata=None)
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'scene.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\nexample')
            client.generate_multimodal('clean desk', [image], 100)
            parts = client.client.models.generate_content.call_args.kwargs['contents'][0].parts
            self.assertEqual(parts[0].text, 'clean desk')
            self.assertEqual(parts[1].inline_data.data, image.read_bytes())
            self.assertEqual(parts[1].inline_data.mime_type, 'image/png')

    def test_dialog_uses_images_and_text_fallback(self):
        prompter = DialogPrompter(DesktopTask({}), SemanticParser(), Mock(), debug_mode=True)
        prompter.debug_mode = False
        prompter.llm_client = Mock()
        prompter.llm_client.generate_multimodal.return_value = ('ok', {})
        prompter.llm_client.generate.return_value = ('text', {})
        self.assertEqual(prompter.query_once('task', 'agent', 1, ['scene.png'])[0], 'ok')
        prompter.llm_client.generate_multimodal.assert_called_once()
        self.assertEqual(prompter.query_once('task', 'agent', 1)[0], 'text')
        prompter.llm_client.generate.assert_called_once()

    def test_image_prompt_does_not_include_measured_state(self):
        task = DesktopTask({'instruction': 'Close laptop'}, image_input=True)
        prompt = task.get_agent_prompt({'secret_position': [123, 456]}, 'LeftArm')
        self.assertNotIn('secret_position', prompt)
        self.assertIn('initial scene', prompt)
        self.assertIn('Close laptop', prompt)

    def test_image_path_and_instruction_file_validation(self):
        from types import SimpleNamespace
        from run_robopara_dialog import resolve_visual_input
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'scene.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\nexample')
            instruction = Path(directory) / 'task.txt'
            instruction.write_text('整理桌面', encoding='utf-8')
            args = SimpleNamespace(image=[str(image)], instruction=None,
                                   instruction_file=str(instruction))
            images, text = resolve_visual_input(args)
            self.assertEqual(images, [image])
            self.assertEqual(text, '整理桌面')
            image.write_bytes(b'not an image')
            with self.assertRaises(ValueError):
                resolve_visual_input(args)

    def test_office_task_has_instruction_and_default_image(self):
        from prompting.visual_tasks import create_visual_task
        task = create_visual_task('office')
        self.assertIn('marker', task.task_instruction)
        self.assertTrue(Path(task.default_image_paths[0]).is_file())

    def test_visual_runner_records_plans_without_execution(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from run_visual_dialog import _build_parser, run_visual_planning
        with tempfile.TemporaryDirectory() as directory:
            args = _build_parser().parse_args([
                '--task', 'office', '--debug_mode', '--data_dir', directory,
                '--run_name', 'test', '--max_plan_steps', '2'])
            replies = [('EXECUTE\nNAME LeftArm ACTION pick marker\nNAME RightArm ACTION WAIT', {}),
                       ('TASK_DONE', {})]
            console = io.StringIO()
            with redirect_stdout(console):
                with patch.object(DialogPrompter, 'query_once', side_effect=replies) as query:
                    run_dir = run_visual_planning(args)
            self.assertEqual((run_dir / 'terminal.txt').read_text(), console.getvalue())
            self.assertIn('Visual planning status: completed', console.getvalue())
            plan = json.loads((run_dir / 'plan.json').read_text())
            self.assertEqual(plan['status'], 'completed')
            self.assertEqual(len(plan['schedule']['left']), 1)
            self.assertTrue(all(call.kwargs['image_paths'] for call in query.call_args_list))
            second_prompt = query.call_args_list[1].args[0]
            self.assertIn('Accepted Planned Action', second_prompt)
            self.assertNotIn('[Executed Action]', second_prompt)

if __name__ == '__main__':
    unittest.main()
