# `run_visual_dialog.py` 提示词总览

本文汇总当前执行 `python run_visual_dialog.py -t office` 时，发送给 Vertex AI Gemini 的全部提示词来源、运行时拼接方式和动态内容。

## 1. 提示词的实际发送结构

当前视觉规划没有使用 Vertex SDK 的独立 `system_instruction`。项目内部虽然把两部分分别记为 `SystemPrompt` 和 `UserPrompt`，但真正发给模型前会直接拼接：

```text
模型文本输入 = SystemPrompt + UserPrompt
模型多模态输入 = [模型文本输入, 第一人称图片]
```

每一次 `LeftArm` 或 `RightArm` 发言都会重新发送完整文本和相同图片。图片以二进制多模态 Part 发送，本地图片路径不会写入提示词。

调用链如下：

```text
run_visual_dialog.py
  -> OfficeTask.get_action_prompt()       动作选项和输出格式
  -> OfficeTask.get_agent_prompt()        任务、身份、观察和讨论要求
  -> DialogPrompter.compose_system_prompt()
       + 已接受的跨轮规划历史
       + 同一步骤的重规划历史和解析反馈
       + 当前双代理对话
  -> DialogPrompter.prompt_one_dialog_round()
       + 本次发言者的 UserPrompt
  -> VertexGeminiClient.generate_multimodal()
       + 图片 Part
```

## 2. 任务配置类提示词

来源：`prompting/visual_tasks/task_office.py`

### 2.1 任务正文 `task_instruction`

```text
Use the dual-arm robot to complete the following ordered office task:

1. Pick up the marker from the desktop.
2. Put the marker into the pen holder.
3. Close the laptop.
4. Pick up the trash from the desktop.
5. Put the trash into the trash bin.
6. Pick up the mouse from the desktop.
7. Put the mouse on the laptop.
8. Adjust the position of the cup on the desktop.
```

### 2.2 视觉任务规则 `task_rules`

```text
- LeftArm and RightArm are the two arms of the same robot.
- Each EXECUTE response must contain one action for each arm.
- Use WAIT when an arm has no required action in the current planning step.
- The image is the initial observation; no action is physically executed.
- Output TASK_DONE only after the accumulated plan covers all eight items.
```

### 2.3 原子动作名称

这些名称由 `get_action_prompt()` 转换为动作选项，也由解析器用于输出校验：

```text
pick
place
flap_close
adjust
```

`WAIT` 是公共对话协议保留的空闲动作，会自动加入动作选项，不写在任务的 `atomic_action_names` 中。

## 3. 公共动作提示词

模板来源：`prompting/visual_tasks/base.py` 的 `get_action_prompt()`。

针对当前 `office` 任务，运行时生成的完整内容是：

```text
[Action Options]
1) pick
2) place
3) flap_close
4) adjust
5) WAIT, do nothing.
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

这里仅限制 `ACTION` 的首个动作名称；动作后面的自然语言或参数目前不属于提示词层面的固定格式，也不会被解析器校验。

## 4. 双臂代理身份与协作提示词

模板来源：`prompting/visual_tasks/base.py` 的 `get_agent_prompt()`。

这段内容会针对当前发言者动态替换 `{agent_name}`、`{other_agent}`、左右侧身份，并把第 2 节的任务正文和规则插入中间：

```text
You are a robot {agent_name}, collaborating with {other_agent} as the
{arm_side} and {other_side} arms of one shared dual-arm robot.
{task_instruction}
{task_rules}

At the current round:
You can observe the current environment from the attached first-person image.
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

具体替换关系为：

| 当前发言者 | 合作者 | 当前侧 | 合作者侧 |
|---|---|---|---|
| `LeftArm` | `RightArm` | `left` | `right` |
| `RightArm` | `LeftArm` | `right` | `left` |

## 5. 每次模型调用的 UserPrompt

来源：`prompting/dialog_prompter.py` 的 `prompt_one_dialog_round()`。

### 5.1 普通调用

```text
You are {agent_name}, your response is:
```

### 5.2 当前视觉任务的最后一次调用

当调用次数到达 `--max_calls_per_round` 限制时，`office` 支持 `TASK_DONE`，因此改用：

```text
You are {agent_name}, this is the last call, you must end your response by
incorporating all previous discussions and output the best plan via EXECUTE,
or output TASK_DONE if the accumulated plan already covers the complete task.
Your response is:
```

`{agent_name}` 会替换为 `LeftArm` 或 `RightArm`。

## 6. 运行时动态加入的上下文

来源：`prompting/dialog_prompter.py` 的 `compose_system_prompt()`、`get_round_history()` 和 `post_plan_update()`。

### 6.1 跨规划步骤历史 `[History]`

每接受一个双臂动作后，该轮完整对话和接受的动作会加入后续调用：

```text
[History]
== Round#0 ==
[Chat History]
[{agent_name}]:
{model_response}
...
[Accepted Planned Action]
NAME LeftArm ACTION {action}
NAME RightArm ACTION {action}
== Round#1 ==
...
== Current Round ==
```

这里明确使用 `[Accepted Planned Action]`，因为视觉规划不执行动作，也没有从仿真获取新状态。

### 6.2 同一步骤的重规划历史 `[Previous Chat]`

如果最终响应解析失败，下一次重规划会收到之前重规划尝试中的全部模型回复：

```text
[Previous Chat]
[LeftArm]:
{previous_response}
[RightArm]:
{previous_response}
```

### 6.3 当前代理对话 `[Current Chat]`

同一次对话轮中，后发言的代理会看到本轮此前代理的回复：

```text
[Current Chat]
[LeftArm]:
{current_response}
[RightArm]:
{current_response}
```

调用顺序从 `LeftArm` 开始，然后是 `RightArm`。因此通常 `RightArm` 的第一次调用已经包含 `LeftArm` 的本轮回复；继续讨论时，两者都会看到不断累积的当前对话。

### 6.4 解析失败反馈

最终响应未通过 `VisualPlanParser` 时，下一次重规划会加入：

```text
This previous response from [{final_agent}] failed to parse!: '{final_response}'
{parse_error} Re-format to strictly follow [Action Output Instruction]!
```

其中 `{parse_error}` 可能报告：

- 缺少 `EXECUTE`；
- 未知或重复的代理名称；
- 动作为空或无法提取动作名称；
- 使用了不受支持的原子动作；
- 缺少某条机械臂的动作；
- 没有做到每条机械臂恰好一个动作。

`VisualFeedbackManager` 当前不会产生仿真、碰撞、重复动作或执行结果反馈，因此视觉运行不会额外加入这些提示。

### 6.5 通用失败计划提示在视觉链路中的状态

`DialogPrompter` 还保留了原仿真链路的通用模板：

```text
Plans below failed to execute, improve them to avoid collision and smoothly reach the targets:
{failed_plans}
```

但是 `run_visual_dialog.py` 只调用 `post_plan_update()`，不会调用写入执行失败的 `post_execute_update()`，所以当前视觉规划正常运行时不会发送这段内容。

## 7. 最终 SystemPrompt 的拼接顺序

每一次代理调用的 `SystemPrompt` 按以下顺序组成：

```text
1. [Action Options] 和 [Action Output Instruction]
2. [History]（已有接受的规划步骤时）
3. 通用失败计划提示（视觉链路当前不会出现）
4. 当前代理身份、任务正文、任务规则、观察与讨论要求
5. [Previous Chat]（当前步骤发生重规划时）
6. 解析失败反馈（当前步骤发生重规划时）
7. [Current Chat]（本轮已有其他发言时）
```

随后直接追加第 5 节中的 `UserPrompt`，再与图片一起发送给模型。

## 8. 图片相关输入

图片路径配置来源：`prompting/visual_tasks/task_office.py`。

当前默认图片：

```text
rocobench/envs/env_office_p2.jpg
```

提示词只包含下面这句抽象描述：

```text
You can observe the current environment from the attached first-person image.
```

实际图片字节由 `prompting/llm_config.py` 的 `generate_multimodal()` 读取，并在文本 Part 后作为 JPEG、PNG 或 WebP Part 发送。命令行 `--image/-i` 可以覆盖默认图片；无论采用默认值还是覆盖值，本地路径都不会发送给模型。

## 9. 提示词和响应在哪里查看

每次运行后，完整的实际提示词会保存在：

```text
data/visual_dialog/{run_name}/{timestamp}/rounds/round_{NNN}/calls/
```

调用文件名形式为：

```text
replan{重规划序号}_call{调用序号}_agent{代理名}_{时间}.json
```

每个调用 JSON 包含：

- `SystemPrompt`：当次完整系统上下文；
- `UserPrompt`：普通或最后一次发言指令；
- `LeftArm` 或 `RightArm`：模型原始响应；
- usage metadata：Token 使用信息。

每次解析后的反馈另存为：

```text
replan{重规划序号}_feedback_{时间}.json
```

整次运行还会生成 `request.json`、`chat_history.json`、`plan.json` 和 `plan.txt`。其中 `request.json` 是运行配置记录，`plan.json` 按 `schedule.left` / `schedule.right` 分类保存非 `WAIT` 动作，`plan.txt` 保留逐轮文本规划；这些文件都不是额外发送给模型的提示词。

## 10. 修改提示词时对应的代码位置

| 分类 | 文件 | 修改内容 |
|---|---|---|
| 任务正文、任务规则 | `prompting/visual_tasks/task_office.py` | 八步任务、`TASK_DONE` 条件、默认图片 |
| 原子动作名称 | `prompting/visual_tasks/task_office.py` | `atomic_action_names` |
| 公共动作与输出格式 | `prompting/visual_tasks/base.py` | `get_action_prompt()` |
| 双臂身份、图片描述、讨论要求 | `prompting/visual_tasks/base.py` | `get_agent_prompt()` |
| 历史、当前对话、解析反馈拼接 | `prompting/dialog_prompter.py` | `compose_system_prompt()`、`prompt_one_round()` |
| 普通及最后一次 UserPrompt | `prompting/dialog_prompter.py` | `prompt_one_dialog_round()` |
| 图片如何随文本发送 | `prompting/llm_config.py` | `generate_multimodal()` |
| 输出格式及动作名称校验 | `prompting/visual_plan_parser.py` | 解析器；它影响失败反馈，但本身不是固定提示词 |

## 11. 当前没有发送给模型的内容

- 本地图片绝对路径；
- `request.json` 中的 Vertex 项目、区域、模型参数和输出目录；
- `plan.json`、`plan.txt` 等最终结果文件；
- 仿真状态、关节位置、IK、RRT、碰撞信息和视频；
- 原子动作编号 `P1/P2/...`、耗时和 `Single arm` 标注；
- `source`、`target` 的固定参数模式或参数合法性规则；
- `PATH_PLAN_INSTRUCTION`：虽然它仍存在于通用 `DialogPrompter`，但视觉入口设置了 `use_waypoints=False`，所以不会加入视觉提示词。
