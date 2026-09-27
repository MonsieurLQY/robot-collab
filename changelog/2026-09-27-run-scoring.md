# RoboPARA 回合结果加入打分

## 变更摘要

- `run_robopara_dialog.py` 每轮把模型讨论耗时随 `execute` 上报为 `planning_wallclock_s`；
  最后未执行的讨论耗时与 `termination_reason` 随 `close` 上报。平台 `close` 响应返回 `score`。
- `summary.json` 新增 `score`；整次运行写 `score_report.json`（`overall`/`by_task`/`episodes`），
  `aggregate.json` 新增 `score`。终端每个种子打印一行 `seed=N score: goals x/7 ...`，最后打印 `[score]` 总体摘要。
- 新增 `prompting/robopara/scoring.py`：原样复制 RoboPARA `main` 分支的 `robopara2/benchmark/scoring.py`（仅依赖标准库），
  `roco39` 无需安装 `robopara2` 即可汇总，报告格式与平台一致。
- 平台未返回打分（`close` 失败或旧版服务端）时，客户端按最后状态的谓词计算目标完成情况，标记 `extra.source=client_fallback`。
- 服务端改动在 RoboPARA `feat/roco`（见其 `changelog/2026-09-27-run-scoring.md`）。
- 更新 `ROBOPARA_BASELINE.md`「打分」一节；`.gitignore` 放行新测试与本 changelog。

## 验证结果

- 新增 `tests/test_robopara_scoring.py` 2 项：平台打分写入 `summary.json`/`score_report.json` 并打印；`close` 失败时客户端兜底。
- `roco39` 运行 RoboPARA 相关 5 个测试模块共 27 项通过。`unittest discover` 全量 57 项中 2 失败 2 错误，
  与改动前基线一致（`test_terminal_log`、`test_visual_runner`、`test_visual_dialog` 既有问题）。
- 回环端到端：`feat/roco` 真实 HTTP 服务（假运行时）+ 真实客户端，pick/place marker 后得 `goal_ratio=1/7`，
  客户端与平台日志中的 `score` 一致，`phase_wallclock_s` 含 `setup`/`planning`/`execution`。
- 未在真实 ManiSkill 仿真和真实模型上重跑。
