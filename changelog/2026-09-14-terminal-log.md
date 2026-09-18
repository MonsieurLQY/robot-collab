# 提示词比较与终端文本留存

## 变更摘要

- 新增 `DEV_PROMPT_COMPARISON_9943870.md`，比较指定提交与当前工作区：原有仿真提示词不变，区分模型封装变化与新增视觉、平台任务模板。
- 撤回此前对三个提示器新增的终端打印，恢复原来的显示文本。
- 新增 `prompting/terminal_log.py`，复制运行期间已有的标准输出、标准错误及控制台日志，保留格式；退出时恢复输出流，中断保留已写文本，同目录续跑采用追加。
- 接入 `run_visual_dialog.py`、`run_robopara_dialog.py`、`run_dialog.py`，在对应数据目录保存 `terminal.txt`，与原 JSON 报告一起留存。未修改提示词、模型参数及反馈格式。
- 增加记录器测试以及现有视觉、平台测试中的文本一致性检查；为新增测试和本日志添加 Git 忽略例外。

## 验证结果

- `robopara2`：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_terminal_log.py -q`，2 项通过，覆盖输出逐字一致、已有日志处理器、异常恢复和追加。
- `roco39`：`python -m unittest discover -s tests -p test_robopara_dialog.py -v`，8 项通过，包括视觉报告与终端文本共同保存。
- `roco39`：`python -m unittest discover -s tests -p test_planning_limits.py -v`，11 项通过，包括平台失败、预算停止、超时后保存的文本与控制台一致。
- `roco39`：`python -m compileall -q prompting rocobench real_world run_dialog.py run_visual_dialog.py run_robopara_dialog.py` 通过；`git diff --check` 通过。
- 最初尝试在 `roco39` 运行 pytest 时发现未安装；改用已有 `robopara2` 运行独立测试，未安装依赖。

## 验证范围

- 未调用线上模型、启动仿真或操作真实机器人；原 MuJoCo 入口仅完成编译检查。
- 记录范围是进入运行逻辑后的 Python 控制台输出，不包括导入阶段警告、键盘回显或原生库和子进程直接写文件描述符的内容。
- 前一阶段的完整视觉测试曾有两项失败：旧英文指令断言与当前指令文件不符，以及末次调用没有视觉专用 `TASK_DONE` 提示。本次未修改相关提示词或这些断言，未宣称该测试文件全部通过。
