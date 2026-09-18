# 当前提示词与 9943870 的比较

比较日期：2026-09-14。

- 旧版本：`9943870298e650de7ff1f9231ed279b35f196ddc`。
- 当前版本：本地 `dev` 工作区，HEAD 为 `01b3b4a`，包含尚未提交的修改和未跟踪文件。
- 当前提示词原文和动态模板见 [DEV_MODEL_PROMPTS.md](DEV_MODEL_PROMPTS.md)。本文比较源码模板及调用结构，不是两次实际模型请求的日志差异。

## 1. 核心结论

**原有六个 MuJoCo 仿真任务的提示词内容没有变化。** `sort`、`pack`、`cabinet`、`rope`、`sweep`、`sandwich` 的动作规则、任务上下文、机器人身份提示和观测描述保持一致。对应任务文件的差异是 `pydantic.v1` 导入兼容和文件末尾空白，不是提示词改写。

变化集中在两个方面：原有调用从 OpenAI 切换到 Vertex AI Gemini；当前工作区新增了纯视觉 office 规划及 RoboPara 桌面执行任务，它们使用不同的任务提示、动作语法和状态来源。

## 2. 原有路径：哪些相同，哪些不同

| 比较项 | 9943870 | 当前工作区 | 影响 |
| --- | --- | --- | --- |
| 六个仿真任务提示词 | 各任务 `get_action_prompt()`、`get_agent_prompt()` 等 | 相同 | 无静态文案变化 |
| dialog 拼接顺序 | 动作、可选 PATH、历史、失败计划、身份及观测、上次讨论、反馈、当前讨论 | 相同 | 同样的输入状态和历史会产生同样的拼接文本 |
| 常规发言要求 | `You are {agent_name}, your response is:` | 相同 | 无变化 |
| 最后一次发言要求 | 要求综合此前讨论并以 `EXECUTE` 输出最佳计划 | 相同，包括原有拼写 | 无变化 |
| PATH 提示 | 轨迹点、均匀间距、避碰和失败修正说明 | 相同 | 无变化 |
| 解析失败反馈模板 | 引用失败回答并要求重新格式化 | 相同 | 错误内容仍随运行动态变化 |
| 成功执行历史 | `[Chat History]` + `[Executed Action]` | 相同 | 仿真执行历史含义不变 |
| 单线程 plan/chat | 任务、动作、历史、观测、失败/反馈、公共 plan/chat 指令 | 相同 | 仍由一次调用规划全部机器人 |
| dialog API 消息 | OpenAI 单条 `role=system`，内容为两段文本相加 | Gemini `contents` 传入相同拼接文本；图文时显式 `role=user` | 消息角色和客户端封装变化，不能视为完全相同的模型实验 |
| 单线程 API 消息 | OpenAI 单条 `role=system`，内容仅 `system_prompt` | Gemini 传入 `system_prompt + user_prompt` | 正常入口的 `user_prompt` 为空，文本不变；手工传非空值时当前版本会追加 |
| 默认模型 | `gpt-4` | 源码默认 `gemini-3.7-flash`，允许参数覆盖 | 模型行为可能不同；这里只报告本地配置，不验证远端模型可用性 |
| 仿真 runner 输出上限 | dialog 512；plan/chat 1024 tokens | 两者均为 2048 | 回答可更长，继而影响后续历史 |
| dialog 发言次数限制 | 多机器人内层循环可能越过上限 | 内层新增上限检查 | 极限轮次的发言数、后续上下文可能变化 |
| 请求重试 | dialog 单次最多 3 次；单线程默认 3 次 | 无预算时最多 5 次；新增有预算入口禁用嵌套重试 | 属于运行控制差异，不是任务文案变化 |
| 真实机器人路径 | 独立旧 OpenAI 客户端及任务模板 | 提示词、拼接和客户端路径未迁移 | 不应将 Gemini 的变化归到 `real_world` |

两个版本已有的任务级 `CHAT_PROMPT` / `PLAN_PROMPT` 常量，不代表都会进入请求：当前及旧版单线程构建器调用的是 `prompting/plan_prompter.py` 的公共 `get_chat_prompt()` / `get_plan_prompt()`。

默认 `--task` 从旧版本的 `sort_one` 改为 `sort`。这是入口配置变化；即使模板保持一致，选择的任务、种子、观测、参数、模型回答和反馈不同，也会让实际请求不同。

## 3. 新增纯视觉 office 提示词

旧提交没有 `run_visual_dialog.py`、`prompting/visual_tasks/` 和这套 `instruction.txt` 输入链路；这些是当前工作区的新增内容。

| 维度 | 原有仿真提示 | 新增 office 视觉提示 |
| --- | --- | --- |
| 身份 | 不同机器人，名称和能力随任务变化 | 同一机器人上的 `LeftArm`、`RightArm` |
| 任务 | 环境代码中的分拣、装箱等目标 | 读取 `instruction.txt`：关闭电脑、归置 marker、交接并丢弃垃圾、放置鼠标、扶正杯子 |
| 观测 | 从仿真状态生成文字，可能含位置和可达范围 | 每轮附同一初始图片，默认 `env_office_p4.png` |
| 动作 | 各环境定义自己的动作及 PATH 语法 | `pick`、`place`、`flap_close`、`adjust`、`handover`、`WAIT` |
| 历史 | 已执行动作 `[Executed Action]` | 已接受规划 `[Accepted Planned Action]`，明确没有物理执行 |
| 后续状态 | 当前仿真观测 | 根据规划历史推断预期效果，图片不是新观测 |
| 反馈 | 解析、任务、可行性等反馈 | 解析器检查格式；视觉反馈管理器不检查物理可行性 |
| 结束提示 | 各任务的执行/环境流程 | 覆盖任务指令后允许 `TASK_DONE` |

仍沿用原 dialog 的逐个角色讨论、历史、重规划反馈和末次 `EXECUTE` 催促。因此，变化主要来自适配器提供的动作段和身份/任务段。

注意：视觉任务允许 `TASK_DONE`，但共享 dialog 的末次调用指令仍强制要求 `EXECUTE`。这是当前两段提示之间存在的不一致，本次仅记录，没有调整规划行为。

## 4. 新增 RoboPara 桌面执行提示词

旧提交也没有 `run_robopara_dialog.py` 和 `prompting/robopara/adapter.py`。它们使用共享 dialog 流程，但任务是平台提供的桌面清理目标。

主要新增要求如下：

- `[Task]` 使用平台 `reset` 返回的任务 JSON，当前指令覆盖其中的 `instruction` 字段。
- 明确实体别名：`flat_newspaper_trash = trash`、`red_tipped_cup = cup`，交接区域为 `handover_zone`。
- 严格动作语法：`PICK <object> desktop_surface`、`PLACE <held_object> <destination>`、`FLAP_CLOSE laptop`、`ADJUST cup upright`、`HANDOVER <trash|mouse> <left_to_right|right_to_left>`、`WAIT`。
- 两臂交接提案必须完全一致；拿取要求空手，放置要求持有物体，交接要求接收方空手。
- 不输出整任务 schedule、坐标、JSON 或 `TASK_DONE`；以环境成功结束任务。
- 初始图片反复使用，不能将旧图当作当前状态；通过历史和 `[Previous execution result]` 推断变化，包括部分执行失败。
- 上一轮结果包含 `reason`、`execution_success`、`completed`、`failed`、`interrupted`、`diagnostics`；开启无反馈选项后置为 `null`。

适配器支持无图片时提供 `[Current measured state]` JSON；当前 runner 正常启动会补入默认图片，因此实际默认使用初始图片模式，不能仅凭入口注释将它描述为每轮发送真值状态。

## 5. 比较方法与边界

使用 `git diff 9943870298e650de7ff1f9231ed279b35f196ddc -- <路径>` 检查已跟踪文件，并单独读取未跟踪的新入口、任务、适配器和指令文件。普通 `git diff` 不会包含未跟踪文件。

另外通过 Python AST 对旧提交和当前源码做结构比较，排除行号和格式影响。对比范围包括模块级字符串常量，以及存在的 `describe_*`、`get_action_prompt`、`get_agent_prompt`、`get_robot_prompt`、`compose_system_prompt`、历史构建/更新、公共 plan/chat 函数。十个文件共 98 个选中定义全部一致：

- `prompting/dialog_prompter.py`、`prompting/plan_prompter.py`。
- `rocobench/envs/task_{sort,pack,cabinet,rope,sweep,sandwich}.py`。
- `real_world/prompts/dialog_prompter.py`、`real_world/task_blockincup.py`。

API、参数和循环变化另由源码 diff 核对。没有实际运行两个版本的模型，因此不宣称输出质量、成功率或实际 token 用量相同。

## 6. 本次新增终端文本留存

此前新增的提示词和图片路径打印已撤回，三个 prompter 恢复原有终端显示。现在通过 `prompting/terminal_log.py` 将运行阶段现有的 Python 标准输出、标准错误和已经配置的控制台 logging 文本同步复制到 `terminal.txt`，不添加日志前缀、时间戳或提示词。保留原有换行、空格、回车和中文；原来没有显示的文本也不会额外输出。

默认保存位置：

| 入口 | 终端文本 | 同目录报告 |
| --- | --- | --- |
| RoboPara | `data/<run_name>/seed_<seed>/terminal.txt` | `summary.json` |
| 纯视觉规划 | `data/visual_dialog/<run_name>/<timestamp>/terminal.txt` | `summary.json`、`plan.json`、`chat_history.json` |
| 原仿真入口 | `data/<run_name>/terminal.txt` | `args_<timestamp>.json`，具体 episode 报告位于其子目录 |

文件随运行写入并刷新，结束时关闭，避免中断丢失已记录文本。继续运行同一目录时追加。视觉与 RoboPara 的最终状态打印也包含在文件中。可通过已有数据目录参数改变保存根目录，不增加命令行开关。

记录从运行目录创建并进入运行逻辑时开始；不包含导入阶段警告、参数校验前输出、终端对人工键盘输入的回显，也不捕获原生库/子进程绕过 Python 流直接写文件描述符的输出。未捕获异常在退出记录范围后由解释器输出的 traceback 不在记录范围；已处理的失败仍保留运行文本和 JSON 报告。

验证包括控制台文本逐字一致、预先配置的 logging、异常后恢复输出流、同目录追加、视觉完成以及平台失败/超时后的报告和文本共同保存。未调用线上模型或启动物理仿真。
