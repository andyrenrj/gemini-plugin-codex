# Gemini Review for Codex

在 Codex 内通过 Antigravity CLI 调用 Gemini，独立审查代码改动、实施计划和规格文档。使用现有 Antigravity 登录与订阅。

首版支持 macOS、Python 3.10+、Git 和已登录的 `agy`。默认模型为 `gemini-3.8-flash-high`，可显式选择 `gemini-3.8-flash-medium` 或 `gemini-3.1-pro-high`。

## 安装

需要 macOS、Python 3.10+、Git、支持插件的 Codex CLI，以及已登录的 [Antigravity CLI](https://antigravity.google/docs/cli/installation/)。本项目通过 `agy` 使用你的 Antigravity 账号，不需要配置 Gemini API key。模型访问和使用额度由你的账号决定。

```bash
codex plugin marketplace add andyrenrj/gemini-plugin-codex
codex plugin add gemini@gemini-review
```

安装后新建 Codex 任务，选择 `gemini:setup` 检查环境，再运行 `gemini:review` 或 `gemini:audit`。

## 在 Codex 中使用

安装插件后，在新任务中选择 `gemini:review` 或直接说“用 Gemini 审查当前改动”。

| Skill | 用途 |
| --- | --- |
| `gemini:review` | 审查工作区、分支、提交；可指定关注点 |
| `gemini:audit` | 审查 plan/spec，可传多份文档与原始目标 |
| `gemini:setup` | 检查 CLI、登录后的模型访问和只读保护 |
| `gemini:status` | 查询任务进度 |
| `gemini:result` | 读取审查报告并核实发现 |
| `gemini:cancel` | 取消正在运行的任务 |

也可以在 `plugins/gemini/` 目录中直接运行脚本：

```bash
python3 scripts/gemini.py setup
python3 scripts/gemini.py review --repo /path/to/project
python3 scripts/gemini.py review --repo /path/to/project --base main --background
python3 scripts/gemini.py review --repo /path/to/project --commit HEAD --focus '检查竞态和数据丢失'
python3 scripts/gemini.py audit /path/to/plan.md /path/to/spec.md --repo /path/to/project --goal '原始需求'
python3 scripts/gemini.py status JOB_ID --repo /path/to/project
python3 scripts/gemini.py result JOB_ID --repo /path/to/project
python3 scripts/gemini.py cancel JOB_ID --repo /path/to/project
```

不带 `--base`/`--commit` 时，审查 HEAD 到当前工作区的改动，包含暂存、未暂存及未被 Git 忽略的未跟踪文件。`--base` 使用 merge-base 到 HEAD；`--commit` 使用指定提交的第一父提交。后两种不包含工作区改动。`--path` 可重复指定文件范围。

## 运行方式与结果

每次任务先固定输入并记录内容 hash。大文件按约 18,000 字符拆成带原始行号的批次；这是初始批次大小，和模型 token 上限不同。多批审查后会补做跨文件检查，汇总模型可按需读取受只读保护的原始快照。输出超限时，优先用明确的 conversation ID 要求简短续写；若仍失败，在有上限的重试内缩小输入范围。不会自动切换模型。

每次模型响应最多 8 条发现；若还有已确认的问题未能报告，必须标记不完整。最终报告合并各批发现，不再截成 8 条。Codex 随后根据源代码核实位置、影响与修正建议。

任务文件存放在 `~/.cache/gemini-codex/jobs/<job-id>/`，含原始审查输入、每次调用的响应流、错误信息、模型用量和最终报告。目录权限为当前用户独享。文件可能包含项目代码；不上传到插件市场。通过 `GEMINI_PLUGIN_STATE` 可以指定测试用的存储目录。

`completed` 表示请求范围内的批次及汇总均返回有效的完整报告；`partial`、`failed`、`cancelled`、`interrupted` 都不能解释为审查通过。CLI `exit=0` 但模型 `status=ERROR` 同样属于失败。

## 权限与已知限制

运行时以 macOS `sandbox-exec` 禁止审查进程写入项目、Git 元数据及显式审查文档，同时使用 Antigravity plan 模式和 terminal sandbox。Antigravity 的登录信息和会话记录仍写入它自己的目录。插件不更改全局权限配置，不传递自动批准全部工具的选项。

原始材料会通过用户已登录的 Antigravity 发送给 Google。启动审查时，Gemini 可读取指定仓库中相关的源码上下文；启动参数会明确登记该项目目录和输入快照目录。捕获阶段排除凭据类路径、二进制、子模块和未跟踪符号链接，并记入报告；提示词也禁止读取这些凭据路径，但插件不提供通用的敏感信息扫描。若 Antigravity 仍拒绝读取，任务会记录权限失败并停止后续调用。历史提交和当前工作树不一致时，以捕获的改动为审查证据，额外上下文不足会列为限制。

输出格式约束不能消除模型内部的推理 token 上限。重试次数有上限，仍失败时保留已完成部分与错误。跨批次汇总不能保证捕获所有跨文件问题。

## 开发与测试

在本插件目录运行离线测试，不需要 Gemini 登录或 API 请求：

```bash
python3 -m unittest discover -s tests -v
```

插件源码位于 `plugins/gemini/`，Codex 市场清单位于 `.agents/plugins/marketplace.json`。

## 参考项目

设计参考 [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc)，版本 `1.0.6`，commit `db52e28f4d9ded852ab3942cea316258ae4ef346`。借鉴任务状态、结果持久化、结构化审查与宿主模型核验的分工。本插件独立实现 Antigravity 运行层。

参考插件的普通 review 调用 Codex app-server 的原生 `review/start`；本插件用 Antigravity CLI 的通用 agent 调用组织审查，不提供与原生 Codex reviewer 相同的内部流程。这是个人维护的插件，与 OpenAI 或 Google 无官方隶属关系。

接口依据：[Antigravity headless mode](https://antigravity.google/docs/cli/headless/) 和 [permissions](https://antigravity.google/docs/permissions/)。
