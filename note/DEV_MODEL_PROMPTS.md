# dev 分支模型提示词整理

整理日期：2026-09-14。基准：本地 `dev`，HEAD `01b3b4a`。

本文按**当前工作区实际代码**整理，包含未提交修改；不等同于 `git show dev:<文件>` 得到的提交快照。`prompting/dialog_prompter.py`、`prompting/llm_config.py` 等已有本地修改；视觉规划、RoboPara 适配器及 `instruction.txt` 当前为未跟踪文件。这里只新增文档，不修改这些实现。

提示词不是一段固定文本，而是“动作规则 + 当前机器人身份/任务/观测 + 历史对话 + 反馈 + 本次发言要求”的动态组合。本文先解释发送流程，再收录英文原文及动态模板。没有调用模型或启动仿真；坐标、状态、服务端任务和历史的实际值需要查看运行日志。

## 1. 入口与提示词来源

| 入口/模式 | 构建器 | 任务来源 | 输入 |
| --- | --- | --- | --- |
| `run_dialog.py`，dialog | `prompting/dialog_prompter.py` | `rocobench/envs/task_*.py` | 仿真状态生成的文字 |
| `run_dialog.py`，plan/chat | `prompting/plan_prompter.py` | 同上 | 全局任务、文字观测 |
| `run_visual_dialog.py`，office（本地新增） | 同一个 `DialogPrompter` | `prompting/visual_tasks/base.py`、`task_office.py`、`instruction.txt` | 初始图像 + 规划历史 |
| `run_robopara_dialog.py`（本地新增） | 同一个 `DialogPrompter` | `prompting/robopara/adapter.py` 的 `DesktopTask` | 服务端任务 + 初始图像 + 执行结果 |
| `real_world/runners/dialog_runner.py` | `real_world/prompts/dialog_prompter.py` | `real_world/task_blockincup.py` | 检测到的对象 + 人机对话 |

## 2. DialogPrompter：每次调用怎样拼接

`compose_system_prompt()` 按下面顺序组成文本；空历史、未触发的反馈不会出现。

```text
{env.get_action_prompt()}
{PATH_PLAN_INSTRUCTION，仅 use_waypoints=True 时追加}
[History]
== Round#0 ==
{历史轮次内容}
...
== Current Round ==
Plans below failed to execute, improve them to avoid collision and smoothly reach the targets:
{failed_plans，仅有执行失败时}
{env.get_agent_prompt(obs, agent_name)}
[Previous Chat]
{本轮此前重规划尝试的对话}
{本轮此前解析失败/环境验证反馈，仅 use_feedback=True 时}
[Current Chat]
{本次讨论中已经发言的机器人及其回答}
```

最后直接追加本次发言提示：

```text
You are {agent_name}, your response is:
```

当本次讨论达到 `max_calls_per_round` 的最后一次调用时，改为以下原文（保留源码拼写）：

```text
You are {agent_name}, this is the last call, you must end your response by incoporating all previous discussions and output the best plan via EXECUTE.
Your response is:
```

每个机器人依次获得一次调用，身份由 `robot_name_map` 的顺序决定。别人的回答以 `[{agent_name}]:` 开头，作为下一次请求中的文字上下文，而不是独立的 assistant 消息。首次规划尝试中，各机器人都发言后才接受包含 `EXECUTE` 的回答；重规划尝试可以提前接受 `EXECUTE`。最终计划经过解析和反馈检查，不合格时将反馈带入下一次重规划。

### 历史与反馈原文

仿真或平台执行成功后，历史条目使用：

```text
[Chat History]
{该轮讨论}
[Executed Action]
{已执行计划}
```

纯视觉规划通过 `post_plan_update()` 写入：

```text
[Chat History]
{该轮讨论}
[Accepted Planned Action]
{已接受但没有实际执行的计划}
```

解析失败的反馈模板：

```text
This previous response from [{final_agent}] failed to parse!: '{final_response}'
{parsed_str} Re-format to strictly follow [Action Output Instruction]!
```

环境反馈由对应 feedback manager 动态生成。仿真反馈实现见 [prompting/feedback.py](../prompting/feedback.py) 及各任务 `get_task_feedback()`；平台验证反馈来自 `/v1/planner/validate` 的 `errors`，前缀为 `[Environment Feedback]`。纯视觉的 `VisualFeedbackManager` 总是返回通过和空反馈，不提供物理可行性判断；格式检查由解析器负责。

## 3. API 实际收到的结构

仿真、视觉和 RoboPara 当前使用 [prompting/llm_config.py](../prompting/llm_config.py) 的 `VertexGeminiClient`：

```python
# 纯文本
client.models.generate_content(
    model=...,
    contents=system_prompt + user_prompt,
    config=...,
)

# 图文：文字在前，各张图片依次在后
contents = [types.Content(role="user", parts=[
    types.Part.from_text(text=system_prompt + user_prompt),
    types.Part.from_bytes(data=image_bytes, mime_type=...),
    # 其余图片
])]
```

因此日志字段 `SystemPrompt` / `UserPrompt` 是程序内部标签；这里没有分别设置 API system/user 消息，也没有单独设置 `system_instruction`。图片以字节附件发送，不是把图片路径当成视觉内容。

`real_world` 是独立的旧路径：`openai.ChatCompletion.create()` 使用一个 `role="system"` 消息，内容同样是 `system_prompt + user_prompt`。不能把它与 Gemini 路径混为一谈。

## 4. 当前双臂 office 视觉任务

默认图像为项目根目录的 `env_office_p4.png`，可通过运行参数指定其他图像。每轮复用初始图片，不会产生执行后的新观测。

- 两个角色：`LeftArm`、`RightArm`，属于同一台双臂机器人。
- 动作库：`pick`、`place`、`flap_close`、`adjust`、`handover`，另加 `WAIT`。
- 每轮每只手臂恰好一个动作，不能把多个操作合并成一个动作。
- 任务正文来自 `instruction.txt`；`task_description` 是元数据，不是此路径额外注入的任务提示。
- 按 `[History]` 中已接受动作的预期效果继续规划，不声称已物理执行。
- 任务规则允许覆盖全部要求后输出 `TASK_DONE`。普通轮次仍要求 `EXECUTE` 加两行手臂动作。

任务正文原文：

```text
'1. flap_close("laptop") (Single arm, 8.5 seconds) '
'2. pick("desktop_surface", "marker") (Single arm, 4 seconds) '
'3. place("marker", "pen_holder") (Single arm, 4.5 seconds) '
'4. pick("desktop_surface", "flat_newspaper_trash") (Single arm, 4 seconds) '
'5. handover("flat_newspaper_trash", "", target_region)'
'6. place("flat_newspaper_trash", "trash_bin") (Single arm, 4 seconds) '
'7. pick("desktop_surface", "mouse") (Single arm, 4 seconds) '
'8. place("mouse", "laptop") (Single arm, 4 seconds) '
'9. adjust("red_tipped_cup", "upright") (Single arm, 7 seconds)'
```

这些行中的外层单引号和动作耗时也是文件内容，会随任务正文进入提示词。视觉动作列表没有单独定义完整参数语法，要求动作以列出的动作名开头；不要将下一节的平台严格语法套用到这里。

## 5. RoboPara 桌面执行任务

`DesktopTask.get_action_prompt()` 规定严格参数格式，并将 `flat_newspaper_trash` 映射为 `trash`、`red_tipped_cup` 映射为 `cup`，交接区域别名为 `handover_zone`。交接方向必须按物体所属手臂决定；双臂必须输出完全相同的交接动作，平台只执行一次。

与纯视觉规划不同，这里禁止输出整任务 schedule、坐标、JSON 或 `TASK_DONE`；结束条件由环境成功信号决定。手臂各持一个对象，拿取、放置、交接都受物体所有权约束，鼠标应放在已关闭的笔记本电脑上。

身份提示依次包含：

```text
You are {agent_name}, the physical {left/right} arm.
[Task]
{服务端 reset 返回的 task JSON，instruction 字段由当前任务指令覆盖}
[Initial image observation]
{初始图片会复用，应通过历史和执行反馈判断变化的说明}
[Previous execution result]
{上一轮执行结果 JSON；首次为 null}
{要求考虑部分失败、另一只手臂职责并协商联合动作}
```

适配器另有无图片时的 `[Current measured state]` JSON 分支，但当前 runner 默认补入图片并校验，因此正常入口使用初始图片模式。`--no-feedback` 会关闭平台预验证、将上一轮执行反馈置为 `null`；`--no-history` 会去掉轮次历史。`--single-agent` 只让 LeftArm 发言，但计划仍需要包含两只手臂。

## 6. 六个仿真任务的差异

| 任务 | 身份提示的核心约束 | 动态信息 |
| --- | --- | --- |
| `sort` | 三机器人分拣；只能操作可达 panel；互相转运帮助；不能全员 WAIT | 方块位置、各自目标、可达 panel、夹爪状态 |
| `pack` | 双机器人装箱；避碰、平滑路径；夹爪高于物体及桌面且低于 0.8 | 物体、箱内槽位坐标、夹爪、桌面高度 |
| `cabinet` | 三机器人开柜并保持门打开，取杯放到对应杯垫；遵守可达范围 | 柜门、杯子、机器人状态；可达对象随柜子位置变化 |
| `rope` | 双机器人同时抓放绳索，越过障碍，保持夹爪间距；高度低于 0.55 且高于桌面 | 绳端、槽端、障碍角点、夹爪坐标 |
| `sweep` | Alice 持簸箕，Bob 持扫帚；移动到同一方块两侧配合清扫 | 方块及机器人状态 |
| `sandwich` | 按配方顺序堆叠；各自在桌子一侧取食材；同一时刻仅一机器人 PUT | 配方、自己可见且可达的食材、夹爪状态 |

各任务的动作大小写、参数、PATH 要求并不统一，准确格式见附录中的 `get_action_prompt()` 及其返回常量。`dialog` 直接使用 `get_agent_prompt()`，并不是把所有 `TASK_CONTEXT`、`CHAT_PROMPT`、`PLAN_PROMPT` 常量都发送一遍。

## 7. 单线程 plan/chat 与真实机器人路径

`SingleThreadPrompter.compose_system_prompt()` 的顺序为：

```text
{env.describe_task_context()}
{env.get_action_prompt() + 可选 PATH 指令}
{可选 History}
{env.describe_obs(obs)}
{执行失败计划（若有）}
Previous Plans Require Improvement:
{本轮此前反馈（若有）}
{get_plan_prompt(env) 或 get_chat_prompt(env)}
```

`plan` 让一次模型调用为全部机器人规划；`chat` 让一次调用生成完整多机器人讨论及最终计划。这里使用 `prompting/plan_prompter.py` 的两个公共函数，不调用各环境自己的 `chat_mode_prompt()` / `central_plan_prompt()`。附录中的任务级同名用途常量应视为备用定义，不能据其存在判断实际发送。

真实机器人对话构造顺序与 dialog 类似，但身份段来自 `env.get_robot_prompt(obs.objects)`，任务为人类 Alice 与机器人 Bob 合作处理杯中方块。Alice 的发言可以来自终端人工输入；模型请求使用单独的旧 OpenAI 客户端路径。相关模板也在附录收录。

## 8. 怎样核对某次运行的完整提示词

查看 runner 传入 `save_path` 下的 `replan*_call*_agent*.json`；单线程日志为 `replan{序号}_{时间}.json`。视觉入口位于 `data/visual_dialog/<运行名>/<时间>/rounds/round_*/calls/`，RoboPara 位于 `data/<运行名>/seed_<种子>/round_<序号>/`（默认数据目录）。

日志中的 `SystemPrompt.message + UserPrompt.message` 就是该次请求的拼接文字；图像还需结合运行的 `request.json` 中路径核对。`*_feedback_*.json` 保存解析/环境反馈。文档中的变量模板不能替代一次真实请求的动态观测与历史。

## 9. 原文与模板附录

以下直接从当前源码提取。静态字符串展示实际字符串值；动态函数展示 Python 原始实现，以保留变量替换、条件分支和拼接顺序。Python 注释不属于发送内容。任务文件中的静态常量包含备用定义，是否实际使用应以本节 `get_action_prompt()`、`describe_task_context()` 及前文调用链为准。

### prompting/dialog_prompter.py

来源：[prompting/dialog_prompter.py](../prompting/dialog_prompter.py)。

#### `PATH_PLAN_INSTRUCTION`

```text
[Path Plan Instruction]
Each <coord> is a tuple (x,y,z) for gripper location, follow these steps to plan:
1) Decide target location (e.g. an object you want to pick), and your current gripper location.
2) Plan a list of <coord> that move smoothly from current gripper to the target location.
3) The <coord>s must be evenly spaced between start and target.
4) Each <coord> must not collide with other robots, and must stay away from table and objects.  
[How to Incoporate [Enviornment Feedback] to improve plan]
    If IK fails, propose more feasible step for the gripper to reach. 
    If detected collision, move robot so the gripper and the inhand object stay away from the collided objects. 
    If collision is detected at a Goal Step, choose a different action.
    To make a path more evenly spaced, make distance between pair-wise steps similar.
        e.g. given path [(0.1, 0.2, 0.3), (0.2, 0.2. 0.3), (0.3, 0.4. 0.7)], the distance between steps (0.1, 0.2, 0.3)-(0.2, 0.2. 0.3) is too low, and between (0.2, 0.2. 0.3)-(0.3, 0.4. 0.7) is too high. You can change the path to [(0.1, 0.2, 0.3), (0.15, 0.3. 0.5), (0.3, 0.4. 0.7)] 
    If a plan failed to execute, re-plan to choose more feasible steps in each PATH, or choose different actions.
```

#### `compose_system_prompt()`

```python
def compose_system_prompt(
        self, 
        obs: EnvState, 
        agent_name: str,
        chat_history: List = [], # chat from previous replan rounds
        current_chat: List = [],  # chat from current round, this comes AFTER env feedback 
        feedback_history: List = []
    ) -> str:
        action_desp = self.env.get_action_prompt()
        if self.use_waypoints:
            action_desp += PATH_PLAN_INSTRUCTION
        agent_prompt = self.env.get_agent_prompt(obs, agent_name)
        
        round_history = self.get_round_history() if self.use_history else ""

        execute_feedback = ""
        if len(self.failed_plans) > 0:
            execute_feedback = "Plans below failed to execute, improve them to avoid collision and smoothly reach the targets:\n"
            execute_feedback += "\n".join(self.failed_plans) + "\n"

        chat_history = "[Previous Chat]\n" + "\n".join(chat_history) if len(chat_history) > 0 else ""
            
        system_prompt = f"{action_desp}\n{round_history}\n{execute_feedback}{agent_prompt}\n{chat_history}\n" 
        
        if self.use_feedback and len(feedback_history) > 0:
            system_prompt += "\n".join(feedback_history)
        
        if len(current_chat) > 0:
            system_prompt += "[Current Chat]\n" + "\n".join(current_chat) + "\n"

        return system_prompt
```

#### `get_round_history()`

```python
def get_round_history(self):
        if len(self.round_history) == 0:
            return ""
        ret = "[History]\n"
        for i, history in enumerate(self.round_history):
            ret += f"== Round#{i} ==\n{history}\n"
        ret += f"== Current Round ==\n"
        return ret
```

### prompting/plan_prompter.py

来源：[prompting/plan_prompter.py](../prompting/plan_prompter.py)。

#### `PATH_PLAN_INSTRUCTION`

```text
[How to plan PATH]
Each <coord> is a tuple (x,y,z) for gripper location, follow these steps to plan:
1) Decide target location (e.g. an object you want to pick), and your current gripper location.
2) Plan a list of <coord> that move smoothly from current gripper to the target location.
3) The <coord>s must be evenly spaced between start and target.
4) Each <coord> must not collide with other robots, and must stay away from table and objects.  
[How to Incoporate [Enviornment Feedback] to improve plan]
    If IK fails, propose more feasible step for the gripper to reach. 
    If detected collision, move robot so the gripper and the inhand object stay away from the collided objects. 
    If collision is detected at a Goal Step, choose a different action.
    To make a path more evenly spaced, make distance between pair-wise steps similar.
        e.g. given path [(0.1, 0.2, 0.3), (0.2, 0.2. 0.3), (0.3, 0.4. 0.7)], the distance between steps (0.1, 0.2, 0.3)-(0.2, 0.2. 0.3) is too low, and between (0.2, 0.2. 0.3)-(0.3, 0.4. 0.7) is too high. You can change the path to [(0.1, 0.2, 0.3), (0.15, 0.3. 0.5), (0.3, 0.4. 0.7)] 
    If a plan failed to execute, re-plan to choose more feasible steps in each PATH, or choose different actions.
```

#### `get_chat_prompt()`

```python
def get_chat_prompt(env: MujocoSimEnv):
    robot_names = env.get_sim_robots().keys()
    talk_order_str = ",".join([f"[{name}]" for name in robot_names])
    chat_prompt = f"""
The robots discuss to find the best strategy. They carefully analyze others' responses and use [Environment Feedback] to improve their plan. 
They talk in order {talk_order_str}... Once they reach agreement, they summarize the plan by **strictly** following [Action Output Instruction] to format the output, then stop talking.
Their entire discussion and final plan are:
    """
    return chat_prompt
```

#### `get_plan_prompt()`

```python
def get_plan_prompt(env: MujocoSimEnv):
    return """
Reason about the task step-by-step, and find the best strategy to coordinate the robots. Propose a plan of **exactly** one action per robot.
Use [Environment Feedback] to improve your plan. Strictly follow [Action Output Instruction] to format and output the plan.
Your reasoning and final plan output are:
    """
```

### prompting/visual_tasks/base.py

来源：[prompting/visual_tasks/base.py](../prompting/visual_tasks/base.py)。

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        if self.atomic_action_names:
            action_lines = [
                f"{index}) {action_name}"
                for index, action_name in enumerate(
                    self.atomic_action_names,
                    start=1,
                )
            ]
            action_lines.append(
                f"{len(action_lines) + 1}) WAIT, do nothing."
            )
            action_options = "\n".join(action_lines)
            action_rule = (
                "Only use an action name listed above. Every non-WAIT ACTION "
                "must begin with exactly one listed action name. Any following "
                "description is allowed but is not part of the action name."
            )
        else:
            action_options = (
                "1) <atomic action>: Choose one concise physical operation.\n"
                "2) WAIT, do nothing."
            )
            action_rule = (
                "The atomic action library is unrestricted for this task."
            )

        return f"""
[Action Options]
{action_options}
{action_rule}
Do not combine several physical operations into one action.
Plan exactly one action for each arm at every round.

[Action Output Instruction]
Must first output 'EXECUTE\n', then give exactly one action per robot, put each
on a new line.
Example: 'EXECUTE\nNAME LeftArm ACTION pick marker\nNAME RightArm ACTION WAIT\n'
""".strip()
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(
        self,
        observation: VisualObservation,
        agent_name: str,
    ) -> str:
        if agent_name not in self.robot_name_map.values():
            raise ValueError(f"Unknown visual planning agent: {agent_name}")
        other_agent = next(
            name
            for name in self.robot_name_map.values()
            if name != agent_name
        )
        arm_side = "left" if agent_name == "LeftArm" else "right"
        other_side = "right" if arm_side == "left" else "left"
        return f"""
You are a robot {agent_name}, collaborating with {other_agent} as the
{arm_side} and {other_side} arms of one shared dual-arm robot.
{self.task_instruction.strip()}
{self.task_rules.strip()}

At the current round:
The attached first-person image shows the initial environment, not a new observation.
The actions in [History] are accepted planning steps, not physically executed
actions. Continue planning from their expected effects.
Think step-by-step about the task and {other_agent}'s response. Carefully check
and correct them if they made a mistake.
Improve your plans if given [Environment Feedback].
Respond very concisely but informatively, and do not repeat what others have
said. Discuss with others to come up with the best plan.
Propose exactly one action for yourself at the **current** round, select from
[Action Options].
End your response by either: 1) output PROCEED, if the plans require further
discussion; 2) If everyone has made proposals and got approved, output the
final plan, must strictly follow [Action Output Instruction]!
""".strip()
```

### prompting/visual_tasks/task_office.py

来源：[prompting/visual_tasks/task_office.py](../prompting/visual_tasks/task_office.py)。

#### `task_id`

```text
office
```

#### `task_name`

```text
Dual-arm office desk organization
```

#### `task_description`

```text
Complete the office task from instruction.txt
```

#### `task_rules`

```text
- LeftArm and RightArm are the two arms of the same robot.
- Each EXECUTE response must contain one action for each arm.
- Use WAIT when an arm has no required action in the current planning step.
- The image is the initial observation; no action is physically executed.
- Output TASK_DONE only after the accumulated plan covers all items in the task instruction.
```

### prompting/robopara/adapter.py

来源：[prompting/robopara/adapter.py](../prompting/robopara/adapter.py)。

#### `PROTOCOL`

```text
robopara.planner.v1
```

#### `get_action_prompt()`

```python
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
```

#### `get_agent_prompt()`

```python
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
```

### rocobench/envs/task_sort.py

来源：[rocobench/envs/task_sort.py](../rocobench/envs/task_sort.py)。

#### `SORT_TASK_CONTEXT`

```text
7 panels on the table, ordered left to right: panel1,...,panel7. They form a straight assembly line, panel1 is closed to panel2 and farthest from panel7.
There are 3 cubes, each robot must place their cube on the correct target, their (cube, target_panel) pairs: 
Alice: (blue_square, panel2), 
Bob: (pink_polygon, panel4), 
Chad: (yellow_trapezoid, panel6).
There are 3 robots, each with a limited reach range, this means they can only pick cubes from these panels, and can only place cubes on these panels. The (robot, [reachable_panels]) pairs: 
(Alice, [panel1, panel2, panel3])
(Bob, [panel3, panel4, panel5])
(Chad, [panel5, panel6, panel7])
```

#### `SORT_TASK_DIALOG_PROMPT`

```text

```

#### `SORT_TASK_CHAT_PROMPT`

```text
Robots discuss to find the best strategy. When each robot talk, it reasons about its own capability (e.g. I am Bob, I can only reach panel3), verifies other robots' claims (e.g. I am Alice, this trapezoid cube is indeed too far for Chad), then decide how to best achieve the task and help each other (e.g. therefore I will move it to panel3 so it's closer to Chad). 
Carefully analyze Environment Feedback, Scene Description and others' responses to coordinate together. They talk in order [Alice],[Bob],[Chad],[Alice] ..., then, after everyone agreed, propose **exactly** one ACTION per robot, then stop talking. 
Their entire chat history and the final plan are:
```

#### `SORT_TASK_PLAN_PROMPT`

```text
Think step-by-step and reason about the best strategy for each robot to achieve their goal or best help others. Carefully consider Environment Feedback and Scene Description.
Decide which cubes and panels can be reached by each robot. At each round, plan **exactly** one ACTION per robot.
```

#### `SORTING_ACTION_SPACE`

```text
[Action Options]
1) PICK <object name> PLACE <location>
2) WAIT
Only PICK an object if your gripper is empty. Target <location> for PLACE should be panel or a bin.
[Action Output Instruction]
You must first output 'EXECUTE
', then give **exactly** one action per robot, put each on a new line.
Example: 'EXECUTE
NAME Alice ACTION PICK red_square PLACE panel3
NAME Bob ACTION WAIT
NAME Chad ACTION PICK green_trapezoid PLACE panel6
'
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(self, obs, agent_name, include_response_instructions=True):
        cube_name, bin_name = self.cube_targets[agent_name]
        other_robots = ", ".join(
            [r for r in self.robots.keys() if r != agent_name]
        )
        robot_name = self.get_robot_name(agent_name)
        agent_state = self.describe_robot_state(obs, robot_name)
        agent_state = agent_state.replace(f"{agent_name}'s", "Your")
        
        cube_states = "\n".join(
            [self.describe_cube_state(obs, cube_name) for cube_name in self.cube_names]
        )
        reachable_panels = ", ".join(self.reachable_panels[agent_name])

        agent_prompt = f"""
7 panels on the table, ordered left to right: panel1,...,panel7. They form a straight assembly line, panel1 is closed to panel2 and farthest from panel7.
You are robot {agent_name} in front of {bin_name}. You are collaborating with {other_robots} to sort cubes into their target panels. The task is NOT done until all three cubes are sorted.
At current round: 
{cube_states}
Your goal is to place {cube_name} on {bin_name}, but you can only reach {reachable_panels}: this means you can only pick cubes from these panels, and can only place cubes on these panels.
{agent_state}
Never forget you are {agent_name}! Never forget you can only reach {reachable_panels}!
Think step-by-step about the task and others' response. Carefully check and correct them if they made a mistake. 
Improve your plans if given [Environment Feedback].
"""
        if include_response_instructions:
            agent_prompt += f"""
When you respond, tell others about your goal and all constraints. Respond very concisely but informatively, and do not repeat what others have said.
Discuss with others to come up with the best plan, e.g. if your cube is out of your reach, ask others for help, and you can do the same for them. 
Propose exactly one action for yourself at the **current** round, select from [Action Options].
End your response by either: 1) output PROCEED, if the plans require further discussion, or 2) If everyone has made proposals and got approved, output EXECUTE and the final plan, must strictly follow [Action Output Instruction]!
In the plan, at least one robot should be acting, you can't all WAIT.
"""
# Example response #1:
# [Reasons] I am {agent_name}, I must put blue_square on panel2, but I can't reach blue_square for now. Since Chad needs yellow_trapezoid, I propose to help Chad move it closer. What does everyone think?
# [Proposal] PICK yellow_trapezoid PLACE panel3
# [Decision] PROCEED
# Example response #2:
# [Reasons] I am Chad, My previous proposal was approved and no need for update. I approve the latest proposals from Alice and Bob.
# [Proposal] WAIT 
# [Decision] 
# EXECUTE\nNAME Alice ACTION WAIT\nNAME Bob ACTION PICK blue_square PLACE panel3\nNAME Chad WAIT
        

        # if agent_name == "Alice":
        #     agent_prompt += f"You must put blue_square in panel2" #you can only reach panel2, panel1, panel3. But you can't reach panel5, panel7, or other bins."
        # elif agent_name == "Bob":
        #     agent_prompt += "You must put pink_polygon in panel4" # you can only reach panel4, panel3, panel5. But you can't reach panel1, panel7, or other bins."
        # elif agent_name == "Chad":
        #     agent_prompt += "You must put yellow_trapezoid in panel6" #you can only reach panel6, panel5, panel7. But you can't reach panel1, panel3, or other bins."
 
        return agent_prompt
```

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        return SORTING_ACTION_SPACE
```

#### `describe_task_context()`

```python
def describe_task_context(self):
        return SORT_TASK_CONTEXT
```

### rocobench/envs/task_pack.py

来源：[rocobench/envs/task_pack.py](../rocobench/envs/task_pack.py)。

#### `PACK_TASK_CONTEXT`

```text
[Task Description]
Two robots, Alice and Bob, each stands at a different side of the table, and together pack all the grocery items on the table into a bin.
They choose objects closest to their grippers. At each round, they are given [Scene description], [Environment feedback], and must reason about the task. Each robot does **exactly** one ACTION and PATH per round, their PATHs must avoid collision.
```

#### `PACK_ACTION_SPACE`

```text
[Action Options]
1) PICK <obj> PATH <path>: only PICK if your gripper is empty;
2) PLACE <obj> bin PATH <path>: only if you have already PICKed the object, you can PLACE it into an empty bin slot, do NOT PLACE if another object is already in a slot!

Each <path> must contain exactly four <coord>s that smoothly interpolate between start and goal, coordinates must be evenly distanced from each other.
The robot PATHs must efficiently reach target while avoiding collision avoid collision (e.g. move above the objects' heights).
The PATHs must do top-down pick or place: 
- move directly atop an object by height 0.2 before PICK: e.g. Alice's gripper is at (0, 0, 0.3), banana is at (-0.25, 0.39, 0.29): NAME Alice ACTION PICK banana PATH [(0, 0.1, 0.3),(0, 0.2, 0.49),(-0.1, 0.25, 0.49),(-0.25, 0.39, 0.49)]
- lift an object vertically up before moving it to PLACE: e.g. Bob's gripper is at (0.9, 0, 0.2), bin_front_left is at (0.35, 0.35, 0.43): NAME Bob ACTION PLACE apple bin_front_left PATH [(0.9,0.0,0.5), (0.5, 0, 0.5), (0.2, 0.1, 0.5),(0.35, 0.35, 0.5)]

[Action Output Instruction]
First output 'EXECUTE
', then give exactly one ACTION per robot, each on a new line.
Example: 'EXECUTE
NAME Alice ACTION PICK apple PATH <path>
NAME Bob ACTION PLACE banana bin_back_middle PATH <path>
'
```

#### `PACK_CHAT_PROMPT`

```text
Robots discuss to find the best strategy and path. When each robot talk, it first reflects on the task status and its own capability. 
Carefully consider [Environment Feedback]. Coordinate with others to plan and improve paths following the instructions. They talk in order [Alice],[Bob],[Alice],..., then, after they agreed, plan exactly one ACTION per robot, output an EXECUTE to summarize the plan and stop talking.
Their discussion and the final plan:
```

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        return PACK_ACTION_SPACE
```

#### `describe_task_context()`

```python
def describe_task_context(self):
        return PACK_TASK_CONTEXT
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(self, obs, agent_name):        
        robot_name = self.get_robot_name(agent_name)
        other_robot = "Alice" if agent_name == "Bob" else "Bob"
        object_desp = "\n".join([self.describe_object(obs, name) for name in self.item_names])

        table_height = self.physics.data.body("table_top").xpos[2] + 0.15 
        robot_desp = self.describe_robot_state(obs, robot_name).replace(f"{agent_name}'s", "Your")
        slot_desp = "\n".join(
            [
                f"{slot_name}: ({x:.2f}, {y:.2f}, {z:.2f})" for slot_name, (x,y,z) in self.bin_slot_xposes.items()
            ]
            )

        agent_prompt = f"""
You are {agent_name}, you and robot {other_robot} each stands at a different side of the table, and together you must put all the grocery items into a bin.
Locations of slots in the bin:
{slot_desp}
At current round:
You see the following objects:
{object_desp}
{robot_desp}
Your gripper must move higher than these objects and higher than table height {table_height:.2f}, but move lower than 0.8.
Never forget you are {agent_name}!
Think step-by-step about the task and {other_robot}'s response. Carefully check and correct {other_robot} if they made a mistake. 
Discuss with {other_robot} to come up with the best plan and smooth, collision-free paths. 
Improve your paths if given [Environment Feedback], choose a different object or target slot if needed.

When you respond, tell {other_robot} about your status. Respond very concisely but informatively, and do not repeat what others have said.
Propose exactly one action for yourself at the **current** round, select from [Action Options].
End your response by either: 1) output PROCEED, if the plans require further discussion; 2) If everyone has made proposals and got approved, output the final plan, must strictly follow [Action Output Instruction] and [Path Plan Instruction].
"""
        return agent_prompt
```

### rocobench/envs/task_cabinet.py

来源：[rocobench/envs/task_cabinet.py](../rocobench/envs/task_cabinet.py)。

#### `CABINET_ACTION_SPACE`

```text
[Action Options]
1) PICK <handle>.
2) OPEN <handle>.
3) PICK <object> PLACE <location>, <object> can be either cup or mug; PICK and PLACE is considered one single ACTION, i.e. you must always PICK and PLACE together
4) WAIT: stays at current position, choose WAIT to hold the door open.
<handle> must be either left or right door handle. Only OPEN a door after you already PICKed its handle, after you OPENed a door, must WAIT at the same spot to hold it open. 
<object> must be either mug or cup, <location> must be the correct coaster.

[Action Output Instruction]
Must first output 'EXECUTE
', then give **exactly** one action per robot, put each on a new line.
Example: 'EXECUTE
NAME Alice ACTION PICK mug PLACE mug_coaster
NAME Bob ACTION WAIT
NAME Chad ACTION OPEN left_door_handle
'
```

#### `CABINET_TASK_CONTEXT`

```text
3 robots, Alice, Bob, Chad together must take a mug and a cup out of a cabinet and place them on the correct coasters.
Both left and right cabinet doors should be OPENed and stays open before anything inside can be PICKed and PLACEed. Robots must coordinate to complete the task most efficiently while avoiding collision.
At each round, given 'Scene description' and 'Environment feedback', use it to reason about the task, and improve any previous plans. 
Each robot does **exactly** one ACTION per round, selected from only one of the above 4 options.
```

#### `CABINET_TASK_CHAT_PROMPT`

```text
Robots discuss to find the best strategy. When each robot talk, it must first reflects on the task status, and its own capability. 
Carefully consider environment feedback and others' responses. It must coordinate with other robots' paths to avoid collision. They talk in order [Alice],[Bob],[Chad],[Alice],..., then, after reaching agreement, output an EXECUTE to summarize the plan, and stop talking.
Their chat history and plan are:
```

#### `CABINET_TASK_PLAN_PROMPT`

```text
Reason about the task step-by-step, and find the best strategy to coordinate the robots. Carefully consider environment feedback to improve your plan. Output exactly one optimal ACTION for each robot at the current round.
```

#### `CABINET_ACTION_SPACE_PATH`

```text
Options for <action>:
1) PICK <object> PATH <path>
2) PLACE <object> <location> PATH <path>, where <location> is (x,y,z), only PLACE mug or cup after it's PICKed, don't PLACE handles.
3) OPEN <handle> PATH <path>, specifies a path to open the door handle, the path must form a curve pointing away from the cabinet door, and move the handle only horizontally, the last <coord> should be target handle position.
4) WAIT PATH <path>, which keeps the robot still, and PATH is a repeated path of current position.
Only PICK an object if your gripper is empty, only PLACE object if it's already in your gripper. 
Only OPEN a door after you already PICKed its handle, after you OPENed a door, WAIT at the same spot to hold it open.
Each <path> must contain exactly four coordinates, e.g. PATH [(0.11,0.22,0.33),(0.28,0.22,0.33),(0.33,0.22,0.33),(0.40,0.22,0.33)]
Instruction on output format: first output 'EXECUTE
', then give exactly one action per robot, put each on a new line.
Example: 'EXECUTE
NAME Alice ACTION PICK mug PATH <path>
NAME Bob ACTION WAIT <path>
NAME Chad ACTION OPEN left_door_handle PATH <path>
'
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(self, obs: EnvState, agent_name: str):
        other_robots = [name for name in self.robots.keys() if name != agent_name]
        if self.cabinet_pos[0] < 0: # cabinet on table left side
            if agent_name == "Alice":
                reachables = "left_door_handle, mug, cup"
            elif agent_name == "Bob":
                reachables = "right_door_handle"
            elif agent_name == "Chad":
                reachables = "right_door_handle, mug, cup"
            else:
                raise NotImplementedError
        else: # cabinet on table right side
            if agent_name == "Alice":
                reachables = "right_door_handle, mug, cup"
            elif agent_name == "Bob":
                reachables = "left_door_handle, mug, cup"
            elif agent_name == "Chad":
                reachables = "left_door_handle"

        robot_desps = [self.describe_robot_state(obs, agent_name).replace(f"{agent_name}'s", "Your")]
        for name in other_robots:
            robot_desps.append(
                self.describe_robot_state(obs, name)
            )
        robot_desp = "\n".join(robot_desps)
        
        door_desp = self.describe_cabinet(obs)
        cup_desp = self.describe_cups(obs)

        agent_prompt = f"""
You are {agent_name}, collaborate with {', '.join(other_robots)} to pick a mug and a cup out of cabinet, and place them on correct coasters.
Both left and right cabinet doors should be OPENed and held open, while anything inside can be PICKed.
You must coordinate to complete the task most efficiently while avoiding collision.
You can only reach {reachables}.
At current round:
{door_desp}
{cup_desp}
{robot_desp}
Think step-by-step about the task and others' response. Carefully check and correct them if they made a mistake. 
Improve your plans if given [Environment Feedback].

When you respond, tell others about your goal and all constraints. Respond very concisely but informatively, and do not repeat what others have said.
Discuss together to come up with the best plan. Never forget you are {agent_name}! Never forget you can only reach {reachables}!
Propose exactly one action for yourself at the **current** round, select from [Action Options]. At least one robot should be acting, you can't all WAIT.
End your response by either: 1) output PROCEED, if the plans require further discussion, or 2) If everyone has made proposals and got approved, output EXECUTE and the final plan, must strictly follow [Action Output Instruction]!

"""
        return agent_prompt
```

#### `describe_task_context()`

```python
def describe_task_context(self):
        context = CABINET_TASK_CONTEXT
        return context
```

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        return CABINET_ACTION_SPACE
```

### rocobench/envs/task_rope.py

来源：[rocobench/envs/task_rope.py](../rocobench/envs/task_rope.py)。

#### `ROPE_FRONT_BODY`

```text
CB0
```

#### `ROPE_BACK_BODY`

```text
CB24
```

#### `ROPE_TASK_CONTEXT`

```text
2 robots, Alice and Bob, together must pick up a long rope and put it precisely into a narrow groove slot. They must lift up the rope together, each grasping one side of the rope. 
There's an obstacle block wall between the rope and the groove, so the robots must lift the rope **high** above the obstacle block top before putting it into the groove.
The rope is heavy, so they must hold the rope at the same time, and distance between their grippers must stay fixed, so the rope doesn't drop.
At each round, if given 'Scene description' and 'Environment feedback', use it to reason about the task and improve any previous plans. 
Each robot should reach for the closet target, if a robot failed IK to reach a goal, change strategy to a different action.
```

#### `ROPE_ACTION_SPACE`

```text
[Action Options]
1) PICK <obj> PATH <path>: only PICK if your gripper is empty, <object> can be either rope_front_end or rope_back_end
2) PUT <obj> <location> PATH <path>: PUT on either groove_left_end or groove_right_end only if you have already PICKed the rope, each end can only hold only one end of the rope, not both.
Choose a different <obj> to PICK or PUT if Environment Feedback failed.
Each <path> must contain exactly four coordinates, each must be evenly distanced from each other and interpolates between start and goal.
PATHs must efficiently reach target while avoiding collision avoid collision (e.g. move above the objects' heights).
The PATHs must do top-down pick or place: 
- move directly atop the rope's end by height 0.2 before PICK: e.g. Alice's gripper is at (0, 0, 0.3), rope_front_end is at (-0.25, 0.39, 0.29): ACTION PICK rope_front_end PATH [(0, 0.1, 0.3),(0, 0.2, 0.49),(-0.1, 0.25, 0.49),(-0.25, 0.39, 0.49)]
- lift rope up before moving it to PUT: e.g. Bob's gripper is at (0.9, 0, 0.2), groove_left_end is at (0.35, 0.35, 0.43): ACTION PLACE rope_front_end groove_left_end PATH [(0.9,0.0,0.5), (0.5, 0, 0.5), (0.2, 0.1, 0.5),(0.35, 0.35, 0.5)]

[Action Output Instruction]
First output 'EXECUTE
', then give exactly one ACTION per robot, each on a new line.
Example: 'EXECUTE
NAME Alice ACTION PUT rope_front_end groove_right_end PATH <path>
NAME Bob ACTION PUT rope_back_end groove_left_end PATH <path>
'
```

#### `ROPE_TASK_CHAT_PROMPT`

```text
They discuss to find the best paths. Carefully consider environment feedback and others' responses. Robots must coordinate paths to avoid collision.
They talk in order [Alice],[Bob],[Alice],..., after reaching agreement, plan exactly one ACTION per robot, output an EXECUTE to summarize the plan, and stop talking.
Their chat and final plan are:
```

#### `ROPE_TASK_PLAN_PROMPT`

```text
Plan one action for each robot. Analyze the task status, choose the best ACTION for each robot based on its current capability, and plan PATH that efficiently achieves the task and avoids collision:
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(self, obs: EnvState, agent_name: str):
        robot_name = self.robot_name_map_inv[agent_name]
        other_robot = "Alice" if agent_name == "Bob" else "Bob"
 
        table_height = self.physics.data.body("table_top").xpos[2] + 0.15
        obj_desp = []
        for side in ["left", "right"]:
            sname = f"groove_{side}_end"
            x,y,z = self.groove_pos[sname]
            obj_desp.append(f"{sname}: ({x:.2f}, {y:.2f}, {z:.2f})")
       
        for name, body_name in zip(
            ["rope_front_end", "rope_back_end"],
            [ROPE_FRONT_BODY, ROPE_BACK_BODY],
        ):
            x,y,z = self.physics.data.body(body_name).xpos
            obj_desp.append(f"{name}: ({x:.2f}, {y:.2f}, {z:.2f})")
        
        for name in OBSTACLE_CORNER_NAMES:
            x,y,z = self.physics.data.site(name).xpos 
            obj_desp.append(f"{name}: ({x:.2f}, {y:.2f}, {z:.2f})")
        obj_desp = '\n'.join(obj_desp)
        robot_desp = self.describe_robot_state(obs, robot_name).replace(f"{agent_name}'s", "Your")            

        agent_prompt = f"""
You are {agent_name}, together with {other_robot} you must pick up a long rope and put it precisely into a narrow groove slot. 
You two must lift up the rope together, each grasping one side of the rope. 
There's an obstacle block wall between rope and groove, so both of you must lift the rope **high** above the obstacle block top before putting it into the groove.
The rope is heavy, so you must always pick and put the rope at the same time.
Distance between your grippers must stay fixed, so the rope doesn't drop.

Your gripper must move lower than 0.55 but higher than table height {table_height:.2f}
At the current round: 
{robot_desp}
Object locations: 
{obj_desp}

Never forget you are {agent_name}!
Think step-by-step about the task and {other_robot}'s response. Check and correct {other_robot} if they made a mistake. 
Discuss with {other_robot} to come up with the best plan and smooth, collision-free paths, propose exactly one action for this round. 
Use [Environment Feedback] to improve your actions and waypoint paths. Reach for the closest target, if any waypoint failed IK, change strategy to a different action.

When you respond, tell {other_robot} about your status. Respond very concisely but informatively, and do not repeat what others have said.
Propose exactly one action for yourself at the **current** round, select from [Action Options].
End your response by either: 1) output PROCEED, if the plans require further discussion; 2) If everyone has made proposals and got approved, output the final plan, must strictly follow [Action Output Instruction] and [Path Plan Instruction].
"""
        return agent_prompt
```

#### `describe_task_context()`

```python
def describe_task_context(self):
        context = ROPE_TASK_CONTEXT
        return context
```

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        return ROPE_ACTION_SPACE
```

### rocobench/envs/task_sweep.py

来源：[rocobench/envs/task_sweep.py](../rocobench/envs/task_sweep.py)。

#### `SWEEP_TASK_CONTEXT`

```text
Alice is a robot holding a dustpan, Bob is a robot holding a broom, together they must sweep up all the cubes on the table.
To sweep up a cube, Alice must place the dustpan to one side, while Bob must sweep the cube from the other side into the dustpan.
At each round, given 'Scene description' and 'Environment feedback', use it to reason about the task, and improve any previous plans. Each robot does **exactly** one action per round.
```

#### `SWEEP_ACTION_SPACE`

```text
[Action Options]
1) MOVE <target>, <target> can only be a cube.
2) SWEEP <target>, this moves the groom so it pushes the <target> into dustpan, only Bob can SWEEP, Alice must WAIT in front of the same <target> cube when Bob SWEEP.
3) WAIT, stays at the current spot.
4) DUMP, only when there are one or more cubes in the dustpan, Alice can DUMP it into trash_bin.
Only SWEEP a cube after both robots MOVEed to the cube.
[Action Output Instruction]
Must first output 'EXECUTE
', then give exactly one action per robot, put each on a new line.
Example#1: 'EXECUTE
NAME Alice ACTION MOVE red_cube
NAME Bob ACTION MOVE red_cube
'
Example#2: 'EXECUTE
NAME Alice ACTION WAIT
NAME Bob ACTION SWEEP red_cube
'
Example#3: 'EXECUTE
NAME Alice ACTION DUMP
NAME Bob ACTION MOVE green_cube
'
```

#### `SWEEP_CHAT_PROMPT`

```text
They discuss to find the best strategy. When each robot talk, it first reflects on the task status and its own capability. 
Carefully consider environment feedback and others' responses. Coordinate with other robots to always sweep the same cube.
They talk in order [Alice],[Bob],[Alice],..., then, after reaching agreement, plan exactly one action per robot, output an EXECUTE to summarize the plan, and stop talking.
Their entire chat history and the final plan are:
```

#### `SWEEP_PLAN_PROMPT`

```text
Plan one action for each robot at every round. Analyze the task status and plan for each robot based on its current capability. Make sure they focus on the same cube to sweep.
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(self, obs, agent_name) -> str:
        other_robot = [name for name in self.robots.keys() if name != agent_name][0]
        tool = "dustpan" if agent_name == 'Alice' else "broom"
        if agent_name == 'Alice':
            instruction = f"You must WAIT at the same cube while {other_robot} SWEEPs."
        else: 
            instruction = f"You must move to the same cube as {other_robot} before SWEEP."
        
        agent_state = self.describe_robot_state(obs, agent_name=agent_name)
        agent_state = agent_state.replace(f"{agent_name}'s", "Your")
        cube_states = [self.describe_cube_state(obs, cube_name) for cube_name in self.cube_names]
        cube_states = "\n".join(cube_states)

        agent_prompt = f"""
You are a robot called {agent_name}, and you are collaborating with {other_robot} to sweep up all the cubes on the table.
You hold a {tool}. 
To sweep up a cube, you and {other_robot} must get close to it by MOVE to opposite sides of the same cube. {instruction}
Talk with {other_robot} to coordinate together and decide which cube to sweep up first.
At the current round:
{agent_state}
{cube_states}
Think step-by-step about the task and {other_robot}'s response. Carefully check and correct them if they made a mistake. 
Improve your plans if given [Environment Feedback].
Never forget you are {agent_name}!
Respond very concisely but informatively, and do not repeat what others have said. Discuss with others to come up with the best plan.
Propose exactly one action for yourself at the **current** round, select from [Action Options].
End your response by either: 1) output PROCEED, if the plans require further discussion; 2) If everyone has made proposals and got approved, output the final plan, must strictly follow [Action Output Instruction]!
        """
        return agent_prompt
```

#### `describe_task_context()`

```python
def describe_task_context(self):
        context = SWEEP_TASK_CONTEXT
        return context
```

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        return SWEEP_ACTION_SPACE
```

### rocobench/envs/task_sandwich.py

来源：[rocobench/envs/task_sandwich.py](../rocobench/envs/task_sandwich.py)。

#### `SANDWICH_ACTION_SPACE`

```text
[Action Options]
1) PICK <obj>, Only PICK if gripper is empty. PICK only the correct next item according to the recipe.
2) PUT <obj1> <obj2>. <obj1> can be one of the foods. <obj2> can be food, cutting_board, or table.
3) WAIT, do nothing.
Only one robot can PUT each round. You must PICK up an item before PUT. 
[Action Output Instruction]
Must first output 'EXECUTE
', then give exactly one action per robot, put each on a new line.
Example#1: 'EXECUTE
NAME Chad ACTION PUT bread_slice1 cutting_board
NAME Dave ACTION PICK tomato
'
Example#2: 'EXECUTE
NAME Chad ACTION WAIT
NAME Dave ACTION PUT cheese tomato
'
```

#### `SANDWICH_CHAT_PROMPT`

```text
The robots discuss before taking actions. Carefully consider environment feedback and others' responses, and coordinate to strictly follow the sandwich recipe and avoid collision.
They talk in order [Chad],[Dave],[Chad],..., after reaching agreement, they output a plan with **exactly** one ACTION per robot, and stop talking. Their chat and final plan are:
```

#### `SANDWICH_PLAN_PROMPT`

```text
Plan one ACTION for each robot at every round. The robot ACTIONs must strictly follow the sandwich recipe and avoid collision.
```

#### `get_agent_prompt()`

```python
def get_agent_prompt(self, obs, agent_name: str = "Chad") -> str:
        assert agent_name in self.agent_names, f"Agent {agent_name} is not in the scene"
        other_robot = [r for r in self.agent_names if r != agent_name][0]
        table_side = "left" if agent_name == "Dave" else "right"
        other_side = "right" if agent_name == "Dave" else "left"

        recipe_str = ", ".join(self.recipe_order)
        
        # each agent can only see items on their own side of the table or on the cutting board
        food_states = [] 
        for food in self.food_items:
            desp = self.describe_food_state(obs, food) 
            if (other_side not in desp and len(desp) > 0):
                food_states.append(desp.replace(table_side, "your"))
        food_states = "\n".join(food_states)

        robot_name = self.robot_name_map_inv[agent_name]
        agent_state = self.describe_robot_state(obs, robot_name)
        agent_state = agent_state.replace(f"{agent_name}'s", "Your")
        agent_prompt = f"""
You are a robot {agent_name}, collaborating with {other_robot} to make a [{self.recipe_name}].
Food items must be stacked following this order: {recipe_str}, where bread_slice1 must be PUT on cutting_board. 
You must stay on {table_side} side of the table! This means you can only PICK food from {table_side} side, and {other_robot} can only PICK from the other side.
Only one robot can PUT at a time, so you must coordiate with {other_robot}.

At the current round:
You can see these food items are on your reachable side:
{food_states}
{agent_state}
Think step-by-step about the task and {other_robot}'s response. Carefully check and correct them if they made a mistake. 
Improve your plans if given [Environment Feedback].
Respond very concisely but informatively, and do not repeat what others have said. Discuss with others to come up with the best plan.
Propose exactly one action for yourself at the **current** round, select from [Action Options].
End your response by either: 1) output PROCEED, if the plans require further discussion; 2) If everyone has made proposals and got approved, output the final plan, must strictly follow [Action Output Instruction]!
        """
        return agent_prompt
```

#### `describe_task_context()`

```python
def describe_task_context(self):
        recipe_str = ", ".join(self.recipe_order)
        context = f"""
2 robots, Chad and Dave, together make a [{self.recipe_name}].
Food items must be stacked following this order: {recipe_str}, where bread_slice1 must be PUT on cutting_board. 
Chad can only reach right side of the table, and Dave can only reach left side of the table.
Both robots can PICK food items, or PUT an item atop something; only one robot can PUT at a time. 
At each round, given [Scene description] and [Environment feedback], use it to reason about the task and improve plans.
        """
        return context
```

#### `get_action_prompt()`

```python
def get_action_prompt(self) -> str:
        return SANDWICH_ACTION_SPACE
```

### real_world/task_blockincup.py

来源：[real_world/task_blockincup.py](../real_world/task_blockincup.py)。

#### `TASK`

```text
run_1
```

#### `TASK_CONTEXT`

```text
A human Alice and a robot Bob stand at different sides of a table and work together to move blocks from inside cups to a wooden bin. 
Bob cannot pick blocks when they are inside cups, but can pick blocks when they are on the table. Alice must help Bob by moving blocks from inside cups to the table.
At each round, given [Scene description], use it to reason about the task, and improve any previous plans. 
Each agent does **exactly** one action per round and only oneb agent can move an object in each round.
```

#### `ACTION_SPACE`

```text
[Action Options]
1) PICK <obj> PLACE <where>: robot Bob must decide which block to PICK. To complete the task, Bob must PLACE all blocks in the wooden bin.
2) WAIT: robot Bob can choose to do nothing, and wait for human Alice to move blocks from inside cups to the table.

[Action Output Instruction]
First output 'EXECUTE
', then give exactly one ACTION for the robot.
Example#1: 'EXECUTE
NAME Bob ACTION PICK blue_triangular_block PLACE wooden_bin
'
Example#2: 'EXECUTE
NAME Bob ACTION WAIT
'
```

#### `CHAT_PROMPT`

```text
Bob and Alice discuss to find the best strategy. Robot Bob must carefully consider human Alice's responses, and coordinate with her to complete the task.
They talk in order [Bob],[Alice],..., then, after reaching agreement, plan exactly one action for Bob, output an EXECUTE to summarize the plan, and stop talking.
Their entire chat history and the final plan are:
```

#### `get_robot_prompt()`

```python
def get_robot_prompt(self, objects) -> str:
        # Provide oracle user input to describe the scene if needed
        # object_desp = input("Give objects description: ").replace("\\n", "\n")
        object_desp = self.describe_objects(objects)
        print("Scene description: ", object_desp)
        # breakpoint()
        
        # If task order specified, add to the robot_prompt: "While placing blocks in the wooden bin, make sure you follow the order blue, yellow, red."
        robot_prompt = f"""
You are a robot called {self.robot_name}, and you are collaborating with human {self.human_name} to move blocks from inside cups to a wooden bin.
You cannot pick blocks when they are inside cups, but can pick blocks when they are on the table. {self.human_name} must help you by moving blocks from inside cups to the table.
If there are any blocks on the table, you can PICK blocks from the table and PLACE them in the wooden bin. If there are no blocks on the table, you can only WAIT for the {self.human_name} to move blocks from inside cups to the table.
Only one agent can pick and place in each round. If the human is picking a block, then the robot should wait. If the robot is picking a block, then the human should wait.
Talk with {self.human_name} to coordinate and decide what to do.
At the current round:
{object_desp}
Think step-by-step about the task and {self.human_name}'s response.
Improve your plans if given [Environment Feedback].
Never forget you are {self.robot_name}!
Propose exactly one action for yourself at the **current** round, select from [Action Options].
End your response by either: 1) output PROCEED, if the plans require further discussion; 2) If everyone has made proposals and got approved, output the final plan, must strictly follow [Action Output Instruction]!
        """
        return robot_prompt
```

#### `get_action_prompt()`

```python
def get_action_prompt(self):
        return ACTION_SPACE
```
