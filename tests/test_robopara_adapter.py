"""RoCo's semantic adapter does not need a simulator or model credentials."""
from prompting.robopara.adapter import SemanticParser, DesktopTask


def test_joint_pick_wait():
    ok, _, plans = SemanticParser().parse({}, "EXECUTE\nNAME LeftArm ACTION WAIT\nNAME RightArm ACTION PICK marker desktop_surface")
    assert ok
    assert plans[0].actions[1]["args"] == {"target": "marker", "source": "desktop_surface"}


def test_handover_is_one_action():
    ok, _, plans = SemanticParser().parse({}, "EXECUTE\nNAME LeftArm ACTION HANDOVER trash right_to_left\nNAME RightArm ACTION HANDOVER trash right_to_left")
    assert ok and len(plans[0].actions) == 1
    assert plans[0].actions[0]["agents"] == ["left", "right"]


def test_incomplete_and_disagreeing_responses_rejected():
    parser = SemanticParser()
    for response in ("TASK_DONE", "EXECUTE\nNAME LeftArm ACTION WAIT",
                     "EXECUTE\nNAME LeftArm ACTION HANDOVER trash right_to_left\nNAME RightArm ACTION WAIT",
                     "EXECUTE\nNAME LeftArm ACTION PICK marker\nNAME RightArm ACTION WAIT"):
        assert not parser.parse({}, response)[0]


def test_prompt_contains_live_failure_not_reference_plan():
    task = DesktopTask({"instruction": "clean desk"})
    task.last_execution = {"reason": "skill_failed", "completed": ["right_pick"]}
    prompt = task.get_agent_prompt({"agents": {"right": {"held_objects": ["marker"]}}}, "LeftArm")
    assert "skill_failed" in prompt and "marker" in prompt
    assert "fixed_v1" not in prompt
