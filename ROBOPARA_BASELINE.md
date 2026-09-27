# RoCo 状态输入规划基线

本入口复用 `dev` 的 `DialogPrompter`，运行多代理讨论、联合动作、环境反馈和逐轮重规划。
新实验脚本强制不录像，不创建视频缓存；`--no-record` 可显式传入，但省略也不会录像。
这是 `state_based_shared_executor` 实验，不是 RGB 外部算法评测或原论文配置的完整复现。
原 `run_dialog.py`、MuJoCo 解析器和运动规划保留。

## 分支与环境

- RoCo：当前 `dev`，入口 `run_robopara_dialog.py`。
- RoboPARA：`feat/roco` 分支，工作目录 `/home/lqy/Projects/RoboPARA-2.0`（不再单独建文件夹）。
  与 `main` 等分支在同一文件夹内用 `git switch feat/roco` / `git switch main` 切换；`robopara2` 环境的 editable 安装指向该文件夹，运行的是当前检出的分支。
- 当前机器使用 `roco39` 调用模型，`robopara2` 执行仿真。现有 `roco` 缺少 `google-genai`。
- 以下命令不需要把两个项目安装到同一环境。模型和凭据参数继承现有 Gemini 客户端。

## 启动

终端一：

```bash
cd /home/lqy/Projects/RoboPARA-2.0
git switch feat/roco
conda activate robopara2
python -m scripts.serve_planner_evaluation \
  --port 8010 --max-steps 12000 --round-max-steps 3600 \
  --log-dir /tmp/robopara-planner/logs --no-record
```

终端二：

```bash
cd /home/lqy/Projects/robot-collab
conda activate roco39
python run_robopara_dialog.py
```

按实际配置使用 `--llm-source`、`--credentials-path`、`--vertex-project`、
`--vertex-location`。客户端继承仓库现有默认模型；本次没有验证该模型的线上可用性。
重复运行需使用新 `--run-name`，防止覆盖结果。

`--debug-mode` 可手工输入两臂动作，不调用模型：

```text
左臂：WAIT
右臂：PICK marker desktop_surface
下一轮左臂：WAIT
下一轮右臂：PLACE marker pen_holder
```

交接时双方都输入相同的 `HANDOVER trash right_to_left`，解析后只执行一个双臂技能。
执行请求超时后客户端不自动重试，防止重复移动机器人；该次回合应按错误处理。
服务绑定回环地址、串行处理请求，同一时刻只允许一个回合。

## 对照和统计

- 默认：双代理、历史、环境反馈。
- `--single-agent`：由 LeftArm 对话实例决定两臂联合动作。
- `--no-feedback`：关闭执行前反馈查询及显式执行诊断提示，平台仍强制校验动作；新状态仍可观察。
- `--no-history`：关闭已执行轮次历史提示。
- `--episodes 5 --seed 0`：依次运行种子 0–4。

客户端保存每次模型请求、反馈、每轮结果、模型调用次数、规划耗时和总体成功率。
平台保存实际动作、逐步执行诊断、仿真步数和最终真实成功状态。
结果中的 `planner_stochasticity=true` 表示 MPlib 路径不保证逐位可复现。

### 打分

每回合结束时平台按任务目标谓词给出打分（口径见 RoboPARA `doc/SCORING.md`），汇总谓词 `success` 不计入：

- 目标级：`goal_ratio`，例如 `2/5` 表示 5 个桌面目标完成 2 个；回合级：全部目标完成才算成功。
  目标与 RoboPARA `main` 一致：`marker_in_holder`、`laptop_closed`、`trash_in_bin`、`mouse_on_laptop`、`cup_upright`。
  交接不再是目标（`*_handover_completed` 仍在环境谓词里作诊断），需要时由规划器自行决定是否交接。
- 用时：`planning_wallclock_s`（客户端模型讨论，随 `execute`/`close` 上报）、`execution_wallclock_s`（平台执行）、
  `total_wallclock_s`、`sim_time_s`（步数 × 0.02 s）。
- 位置：每个种子的 `data/<run-name>/seed_<N>/summary.json` 中的 `score`；整次运行的 `data/<run-name>/score_report.json`；
  `aggregate.json` 中的 `score`；平台 `--log-dir` 日志中的 `score`。终端每个种子一行 `seed=N score: ...`，最后一行 `[score] ...` 为总体摘要。
- 平台未返回打分（如 `close` 失败、旧版服务端）时，客户端按最后一次状态自行计算，`extra.source=client_fallback`，此时只有规划用时。

## 当前边界

- 仅支持桌面清理，当前动作集合为 pick/place/handover/flap_close/adjust/wait。
- pick 暂只支持从桌面抓取 marker/trash/mouse/cup；place 只支持三种任务目标放置。
- 使用真值位姿、实测抓持及任务谓词；不提供答案计划。
- 只在回合开始初始化执行器，跨轮保留抓持和夹爪控制。轮内并行、轮间同步。
- 禁用平台自动换手和放置／扶正语义修复；保留底层轨迹规划和抓取候选重试。
- 第一版没有原 MuJoCo 的路径点规划和执行前 IK／碰撞反馈。
- 环境步数上限和轮数上限有效；每轮最多 3600 个控制步。终端两臂 WAIT 只推进一个控制步。

## 已验证

- 语义解析 4 项、原讨论循环反馈重规划与无反馈开关 2 项测试通过。
- 平台新增契约测试 7 项通过，包括真实 HTTP 回环测试。
- 平台全套沙箱测试：72 项通过，25 子测试通过；2 项仅因回环 socket 权限失败。
  新增 HTTP 测试已单独在沙箱外通过，原 RGB HTTP 测试未在本次沙箱外重跑。
- 真实 ManiSkill 种子 0：跨轮抓取 marker、放入笔筒成功，717 个控制步，
  `marker_in_holder=true`，整体 `task_success=false`（此验证只执行了两步）。
- 启动完整 HTTP 仿真服务的权限请求被拒绝，未执行真实模型整回合，也未执行五种子测评。

## 图片环境输入与任务指令

`dev` 已移植 `main` 的静态图片输入、视觉任务定义和独立视觉规划入口。
只生成视觉计划（不启动仿真）：

```bash
conda activate roco39
python run_visual_dialog.py --task office
```

离线视觉入口默认读取项目根目录的 `env_office_p4.png`；两个入口默认读取 `instruction.txt`。
默认路径根据脚本位置解析，不依赖当前工作目录；显式图片与指令参数仍可覆盖默认值。
独立视觉规划生成的 JSON 保持 `main` 格式，不直接作为仿真动作提交。

图片输入接入逐轮仿真：

```bash
python run_robopara_dialog.py
```

- 默认读取平台最新帧，不再使用 `env_office_p4.png`：第一轮用 reset 后的画面，
  之后每轮开始决策前，从上一次响应的 `image.path` 读取 buffer 里的最新一张（buffer 只有一张）。
  平台在 reset、每 10 s 仿真时间和每轮结束时刷新这张图，所以每轮拿到的都是上一轮执行后的桌面。
- 读取的图会复制到本轮日志目录 `round_<n>/scene.jpg`，模型读的就是这份副本；
  buffer 之后被覆盖，日志里仍保留模型当时看到的那张图。每轮的帧信息记录在 `summary.json` 的 `rounds[].image`。
- 平台响应没有图片（服务端以 `--no-frame-buffer` 启动）或图片步数与状态步数不一致（取帧失败）时，
  回合以 `run_error` 结束，不把过期画面发给模型。
- `--image/-i` 改为显式覆盖：传入后每轮都发送这些固定图片，不读取 buffer，支持 PNG/JPEG/WebP，可重复传入多张。
  固定图片是静态参考，不会随执行更新，需要与本次 seed 场景对应。
- 带图片时，环境提示不包含真实物体坐标和机器人状态文本；仍保留平台校验与执行反馈，因此不属于严格 RGB 评测。
- `request.json` 和 `summary.json` 记录输入模式：`latest_frame`（默认）或 `initial_image`（固定图片）。
- `--instruction` 与 `--instruction-file` 二选一；都不传时读取根目录 `instruction.txt`。
  自定义指令只改变模型输入，不能改变平台桌面清理成功条件。
- 图片输入不需要录像；新服务不支持 `--record-dir`。

补充验证：RoCo 的 8 项对话／多模态测试通过，包括模拟 SDK 图片字节传输及离线视觉入口；
平台的 7 项非网络契约测试通过，包括禁止录像测试。未调用线上模型或启动仿真。

## 有限尝试与自动停止

最新默认值（覆盖前述旧运行参数）：

| 限制 | 默认值 | 仿真入口参数 |
| --- | --- | --- |
| 每次讨论的代理发言总数 | 2 | `--max-calls-per-round` |
| 每轮规划尝试数（含首次） | 2 | `--num-replans` |
| 连续失败轮数 | 2 | `--max-consecutive-failures` |
| 回合规划轮数 | 12 | `--max-rounds` |
| 回合模型请求总数 | 30 | `--max-model-calls` |
| 回合墙钟时间 | 600 秒 | `--max-episode-seconds` |
| 单次模型请求时间 | 60 秒 | `--model-timeout` |

默认一轮最多 4 次模型请求；连续两轮无法得到可执行计划即停止，不会再进入下一轮重新尝试。
执行失败也计入连续失败，执行成功后清零；整回合还受总轮数、总请求数和时间上限约束。
失败、超时的模型请求同样占用总次数。此入口关闭应用层和 SDK 的自动 API 重试。

`summary.json` 新增 `termination_reason`、`limits`、`error`、`close_error`。
`model_calls` 记录实际发起的请求数，`saved_model_responses` 记录成功落盘的响应数。
常见停止原因：`model_call_limit`、`consecutive_failure_limit`、`episode_timeout`、
`model_request_timeout`、`max_rounds`。中止时保留已有对话和汇总，不把中止判为成功。

独立入口 `run_visual_dialog.py` 同样默认最多 30 次请求、600 秒、单次 60 秒，
支持 `--max-model-calls`、`--max-episode-seconds`、`--model-timeout`，
每次讨论 2 次发言、每轮 2 次尝试，规划失败即结束并保存 `summary.json`。

时间限制使用 Linux 主线程定时信号，可中断模型和 HTTP 等待。回合结束后另留最多 5 秒关闭服务会话，
随后写日志。若超时时平台已经在执行物理动作，客户端不能强行取消远端动作；
会发送 close 请求，失败记入 `close_error`，本地不再规划或自动重试执行。

修改不会热更新已经运行的 Python 进程。请在旧 RoCo 终端按 Ctrl+C 结束，
确认旧平台会话已关闭；若服务仍被占用，停止并重新启动服务，然后运行：

```bash
python run_robopara_dialog.py
```

本次验证：11 项预算／停止测试、8 项对话／视觉测试、4 项语义解析测试通过。
测试使用模拟模型和平台，不发起真实 API 请求，不启动仿真或录像。
