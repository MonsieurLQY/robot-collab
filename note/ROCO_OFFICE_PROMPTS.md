# RoCo：office 桌面清理提示词

本文依据 2026-09-14 本地 `dev` 工作区代码整理，用于报告当前 `run_visual_dialog.py --task office` 的提示词设计。英文代码块保留实际文案；花括号表示运行时替换项。这里只整理文档，不修改提示词或终端显示。

## 1. 实验设置与报告描述

RoCo 将同一台双臂机器人的左右臂分别建模为 `LeftArm` 和 `RightArm` 两个对话角色。两个角色共享办公室桌面的初始第一视角图像及清理任务指令，依次讨论当前步骤的动作。每轮联合计划为每只手臂分配一个原子动作，空闲手臂使用 `WAIT`。系统将接受的动作及讨论加入历史，使后续规划根据这些动作的预期效果继续。该入口不进行物理执行，也不获取执行后的图像。

默认图片为项目根目录的 `env_office_p4.png`，任务正文来自 `instruction.txt`。提示词要求完成关合笔记本电脑、收纳记号笔、传递并清理垃圾、归置鼠标及扶正杯子。当前指令文件按 9 行操作描述这些要求；这些行不等于模型必须输出的 9 个联合规划轮次。

## 2. Task instructions

来源：[instruction.txt](../instruction.txt)。以下单引号、函数式描述和耗时均是文件正文，会随提示词发送给模型；交接步骤没有给出耗时。

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

## 3. Task rules

来源：[OfficeTask.task_rules](../prompting/visual_tasks/task_office.py)。

```text
- LeftArm and RightArm are the two arms of the same robot.
- Each EXECUTE response must contain one action for each arm.
- Use WAIT when an arm has no required action in the current planning step.
- The image is the initial observation; no action is physically executed.
- Output TASK_DONE only after the accumulated plan covers all items in the task instruction.
```

## 4. Action prompt

来源：[VisualDualArmTask.get_action_prompt()](../prompting/visual_tasks/base.py)，动作列表由 `OfficeTask.atomic_action_names` 提供。代码块中 `EXECUTE` 后的换行是实际字符串中的换行。

```text
[Action Options]
1) pick
2) place
3) flap_close
4) adjust
5) handover
6) WAIT, do nothing.
Only use an action name listed above. Every non-WAIT ACTION must begin with exactly one listed action name. Any following description is allowed but is not part of the action name.
Do not combine several physical operations into one action.
Plan exactly one action for each arm at every round.

[Action Output Instruction]
Must first output 'EXECUTE
', then give exactly one action per robot, put each
on a new line.
Example: 'EXECUTE
NAME LeftArm ACTION pick marker
NAME RightArm ACTION WAIT
'
```

## 5. Agent prompt

来源：[VisualDualArmTask.get_agent_prompt()](../prompting/visual_tasks/base.py)。`{task_instruction}` 和 `{task_rules}` 分别替换为前两节原文，不另外添加标题。

```text
You are a robot {agent_name}, collaborating with {other_agent} as the
{arm_side} and {other_side} arms of one shared dual-arm robot.
{task_instruction}
{task_rules}

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
```

两个角色的替换值：

| 变量 | 左臂发言时 | 右臂发言时 |
| --- | --- | --- |
| `{agent_name}` | `LeftArm` | `RightArm` |
| `{other_agent}` | `RightArm` | `LeftArm` |
| `{arm_side}` | `left` | `right` |
| `{other_side}` | `right` | `left` |

## 6. 本次发言要求

共享 [DialogPrompter](../prompting/dialog_prompter.py) 在每次请求的末尾追加以下文本：

```text
You are {agent_name}, your response is:
```

当达到当前讨论的最后一次允许调用时，替换为下列文本（保留源码中的 `incoporating` 拼写）：

```text
You are {agent_name}, this is the last call, you must end your response by incoporating all previous discussions and output the best plan via EXECUTE.
Your response is:
```

默认每次讨论允许 2 次模型调用，因此通常由 LeftArm 先发言，RightArm 收到末次调用要求；每轮最多尝试 2 次规划。重规划尝试也重新应用同一套发言要求。

## 7. 完整请求的拼接方式

下图式文本仅说明拼接顺序，花括号及条件说明不会原样发送；具体历史和反馈随每轮结果变化。

```text
{Action prompt}
{历史轮次，如果已有接受的规划}
{Agent prompt，已替换角色、Task instructions 和 Task rules}
{Previous Chat，如果本轮已经有过重规划尝试}
{解析或环境反馈，如果存在}
{Current Chat，如果本次讨论中已有角色发言}
{本次发言要求}
```

office 入口固定关闭额外 PATH 指令，并开启历史与反馈拼接。首次 LeftArm 请求没有历史、此前讨论或反馈，文本就是动作提示、替换后的左臂提示和本次发言要求。RightArm 的请求还会加入 LeftArm 的回答。

历史轮次格式为：

```text
[History]
== Round#0 ==
[Chat History]
{此前该轮各角色的回答}
[Accepted Planned Action]
{此前该轮已接受的联合计划}
== Current Round ==
```

本次讨论的回答使用以下形式拼入后续请求：

```text
[Current Chat]
[LeftArm]:
{左臂本次回答}
```

格式解析失败时，反馈使用以下模板：

```text
This previous response from [{final_agent}] failed to parse!: '{final_response}'
{parsed_str} Re-format to strictly follow [Action Output Instruction]!
```

文本最终以 `system_prompt + user_prompt` 拼接，与初始图片一起发送给 Gemini。尽管日志内部称为 `SystemPrompt` 和 `UserPrompt`，多模态 API 实际使用一条 `role="user"` 消息：先放完整文本，再放图片字节附件；没有独立的 system 消息。

## 8. 报告中需要准确区分的内容

- **规划与执行**：历史中的动作是已接受的计划，不能报告为机器人已执行成功。JSON 中的 schedule 是程序汇总结果，提示词要求模型逐轮给出联合动作，并非一次输出完整 schedule JSON。
- **初始图片与实时观测**：每轮复用初始图片，后续状态根据历史动作的预期效果推断。
- **格式反馈与物理反馈**：此入口的 `VisualFeedbackManager` 返回通过和空反馈；解析器检查格式及动作名称，但没有通过仿真验证碰撞、可达性或任务实际完成情况。
- **完成条件与末次指令**：任务规则允许全部要求覆盖后输出 `TASK_DONE`，但共享末次发言提示仍要求以 `EXECUTE` 给出计划。两段指令存在不一致；报告应按当前实现描述，本次没有修改它。
- **与 RoboPara 入口区分**：`run_robopara_dialog.py` 的平台执行版本另有严格动作参数、物体别名及执行反馈，并禁止 `TASK_DONE`。不能将其提示词与本文纯视觉 office 版本混写。

## 9. 相对旧总结的修正

| 旧总结 | 当前代码 |
| --- | --- |
| `covers all eight items` | `covers all items in the task instruction`；指令文件当前为 9 行 |
| 动作列表只有 `pick/place/flap_close/adjust/WAIT` | 新增 `handover`，`WAIT` 顺延为第 6 项 |
| `You can observe the current environment from the attached first-person image.` | `The attached first-person image shows the initial environment, not a new observation.` |

其他参考：[项目提示词总览](DEV_MODEL_PROMPTS.md)、[与 9943870 提交的对比](DEV_PROMPT_COMPARISON_9943870.md)。
