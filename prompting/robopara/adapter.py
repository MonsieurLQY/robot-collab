"""Transport, semantic parsing and state prompts without simulator imports."""
import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROTOCOL = "robopara.planner.v1"
AGENTS = {"LeftArm": "left", "RightArm": "right"}
LINE = re.compile(r"^NAME\s+(LeftArm|RightArm)\s+ACTION\s+(.+)$")


class PlannerClient:
    def __init__(self, url, timeout=900):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.episode_id = None
        self.round_id = 0

    def call(self, operation, **payload):
        request = Request(self.url + "/v1/planner/" + operation,
                          data=json.dumps({"protocol_version": PROTOCOL,
                                           "episode_id": self.episode_id,
                                           "round_id": self.round_id, **payload}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(exc.read().decode()) from exc
        if result.get("protocol_version") != PROTOCOL:
            raise RuntimeError("platform protocol mismatch")
        if "error" in result:
            raise RuntimeError(result["error"])
        self.episode_id = result.get("episode_id", self.episode_id)
        self.round_id = result.get("round_id", self.round_id)
        # Never retry execute automatically: a timed-out request may have moved robots.
        return result


@dataclass
class JointAction:
    actions: list
    parsed_proposal: str

    def get_action_desp(self):
        return self.parsed_proposal


class SemanticParser:
    def parse(self, _obs, response):
        try:
            if "EXECUTE" not in response:
                raise ValueError("Response must contain EXECUTE")
            parsed = {}
            lines = response.rsplit("EXECUTE", 1)[1].strip().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                match = LINE.fullmatch(line.strip())
                if match is None:
                    raise ValueError("Use NAME LeftArm/RightArm ACTION <skill> <arguments>")
                name, text = match.groups()
                if name in parsed:
                    raise ValueError("Each arm must appear exactly once")
                parsed[name] = text.split()
            if set(parsed) != set(AGENTS):
                raise ValueError("Both LeftArm and RightArm are required; use WAIT for idle arm")
            actions = []
            handovers = []
            for name, words in parsed.items():
                skill, values, arm = words[0].lower(), words[1:], AGENTS[name]
                keys = {"pick": ("target", "source"), "place": ("source", "target"),
                        "flap_close": ("object",), "adjust": ("object", "target_state"),
                        "handover": ("object", "direction"), "wait": ()}
                if skill not in keys or len(values) != len(keys[skill]):
                    raise ValueError("Invalid action arguments; follow the action grammar")
                args = dict(zip(keys[skill], values))
                if skill == "handover":
                    handovers.append(args)
                actions.append({"skill": skill, "agents": [arm], "args": args})
            if handovers:
                if len(handovers) != 2 or handovers[0] != handovers[1]:
                    raise ValueError("Both arms must propose the same HANDOVER object direction")
                actions = [{"skill": "handover", "agents": ["left", "right"],
                            "args": handovers[0]}]
            return True, "", [JointAction(actions, response.rsplit("EXECUTE", 1)[1].strip())]
        except (ValueError, IndexError) as exc:
            return False, str(exc), []


class PlatformFeedback:
    def __init__(self, client, enabled=True):
        self.client = client
        self.enabled = enabled

    def give_feedback(self, plan):
        if not self.enabled:
            return True, ""
        result = self.client.call("validate", actions=plan.actions)
        return result["valid"], "[Environment Feedback]\n" + "\n".join(result["errors"])


class DesktopTask:
    def __init__(self, task, image_input=False):
        self.task = task
        self.image_input = image_input
        self.last_execution = None

    def get_action_prompt(self):
        return """[Action Options and Output Instruction]
Choose one current action for each arm. Use exact object IDs from the task.
Instruction aliases: flat_newspaper_trash = trash; red_tipped_cup = cup;
target_region in a handover denotes handover_zone.
Translate instruction function notation into the action grammar below;
choose the actual handover direction from arm ownership.
PICK <object> desktop_surface
PLACE <held_object> <destination>
FLAP_CLOSE laptop
ADJUST cup upright
HANDOVER <trash|mouse> <left_to_right|right_to_left>
WAIT
For HANDOVER both arms must output exactly the same action; it executes once.
Each arm can hold one object. Pick requires an empty hand. Place requires ownership.
Handover requires donor ownership and an empty receiver. Place mouse on a closed laptop.
Use WAIT when the other arm should work alone. Nonconflicting actions can run in parallel.
Discuss observations, reachable choices and conflicts with the other arm, then agree:
EXECUTE
NAME LeftArm ACTION <action>
NAME RightArm ACTION <action>
Do not output a whole-task schedule, coordinates, JSON or TASK_DONE.
Environment success, not your claim, ends the episode.
"""

    def get_agent_prompt(self, obs, agent_name):
        observation_text = (
            "[Initial image observation]\nThe attached images show the initial scene. "
            "They are reused, not updated after execution. Use action history and execution "
            "feedback to reason about changes; do not treat old pixels as the current state."
            if self.image_input else "[Current measured state]\n" + json.dumps(obs, ensure_ascii=False)
        )
        return ("You are " + agent_name + ", the physical " + AGENTS[agent_name] + " arm.\n"
                + "[Task]\n" + json.dumps(self.task, ensure_ascii=False)
                + "\n" + observation_text
                + "\n[Previous execution result]\n" + json.dumps(self.last_execution, ensure_ascii=False)
                + "\nPlan from the current state, including partial failures. "
                "No geometric feasibility guarantee is supplied. "
                "Keep the other arm's responsibilities in mind and negotiate a joint action.")
