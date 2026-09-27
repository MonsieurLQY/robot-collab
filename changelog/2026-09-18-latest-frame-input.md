# RoboPARA 逐轮仿真改为读取平台最新帧

## 变更摘要

- `run_robopara_dialog.py` 不再默认读取 `env_office_p4.png`，改为读取 RoboPARA 规划服务的最新帧 buffer：
  第一轮使用 reset 响应里的图片，之后每轮开始决策前使用上一次 `execute` 响应里的图片。
  buffer 只保留一张，路径取自响应的 `image.path`，不在 RoCo 里写死。
- 每轮把 buffer 图片复制为 `round_<n>/scene.jpg`，模型读取这份副本，保证日志中的图就是模型看到的图；
  `summary.json` 的 `rounds[].image` 记录每轮帧的 `sequence`、`round_id`、`steps`、`sim_time_s` 和副本路径。
- 平台响应缺少图片，或图片步数与状态步数不一致（平台取帧失败）时抛出明确错误，回合以 `run_error` 结束，
  不向模型发送过期画面。
- `--image/-i` 改为显式覆盖：传入后行为与之前相同，每轮发送固定图片，不读取 buffer。
- `DesktopTask` 新增 `live_image` 参数；实时模式的提示说明附图是当前画面、每轮刷新，
  不再沿用"初始图片、执行后不更新"的说明。固定图片模式的提示保持不变。
- 输入模式标记新增 `latest_frame`（默认）；固定图片仍为 `initial_image`。
- 更新 `ROBOPARA_BASELINE.md` 的图片输入说明。离线入口 `run_visual_dialog.py` 不接仿真，仍默认使用 `env_office_p4.png`。
- `.gitignore` 为新测试文件和本 changelog 增加忽略例外，沿用仓库只放行指定文件的做法。
- RoboPARA 平台与规划服务端无改动。

## 验证结果

- 新增 `tests/test_robopara_live_image.py` 6 项：首轮读 reset 帧、后续轮读最新帧、模型读取的副本与日志一致、
  缺图时明确报错、过期帧不发给模型、`--image` 覆盖时不读 buffer、默认不再读取办公室图片、实时提示词。
  `tests/test_planning_limits.py` 的假服务端响应补充 buffer 图片。
- `roco39` 运行 `python -m unittest discover -s tests`：55 项，2 项失败、2 项错误。与改动前基线（49 项，
  同样 2 项失败、2 项错误）逐项一致，均为既有问题：`test_terminal_log` 缺 `pytest`、
  `test_visual_runner` 缺 `max_model_calls`、`test_visual_dialog` 两项断言与当前视觉任务文本不符；与本次改动无关。
- `robopara2` 运行 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_robopara_adapter.py -q`：4 项通过。
- `compileall` 与 `git diff --check` 通过。
- 真实端到端：启动 `feat/planner-baseline` 规划服务（`988cfb0`，开启 buffer），RoCo 以 `--debug-mode`
  通过 HTTP 手工输入两轮动作，种子 0。第 0 轮读取第 0 步的 reset 帧，右臂抓取 marker（到 278 步）；
  第 1 轮读取第 278 步的帧，放入笔筒（到 717 步），`marker_in_holder=true`。两轮副本内容不同，
  人工查看第 1 轮图片确认 marker 已被右臂夹起、不在桌面。输入模式记录为 `latest_frame`，无错误。

## 已知限制

- 端到端验证使用 debug 模式手工输入动作，未调用 Gemini，未完成完整桌面清理任务或多种子评测。
- 带图片时仍不把实时状态 JSON 放入提示，模型依赖图片、执行结果和历史；这一实验设定未改变。
- 规划服务端串行处理：只在每轮开始时读取最新帧，执行中途不读取，也无法中断当前轮。
- 未提交、未推送。
