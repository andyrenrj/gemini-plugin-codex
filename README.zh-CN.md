# Gemini Review for Codex

[English](README.md) | 简体中文

在 Codex 中通过 Gemini 独立审查代码改动、实施计划和规格文档。插件调用 Antigravity CLI，使用你已有的登录状态与订阅。

## 环境要求

- macOS，通过 `sandbox-exec` 禁止写入审查项目
- Python 3.10 或更高版本，以及 Git
- 支持插件的 Codex CLI
- 已安装并登录的 [Antigravity CLI](https://antigravity.google/docs/cli/installation/)（`agy`）

无需配置 Gemini API key。可用模型和使用额度取决于你的 Antigravity 账号。

## 安装

```bash
codex plugin marketplace add andyrenrj/gemini-plugin-codex
codex plugin add gemini@gemini-review
```

安装后新建一个 Codex 任务，先运行 `gemini:setup` 检查环境，再使用 `gemini:review` 或 `gemini:audit`。

如果此前安装过本地开发版，在安装 GitHub 版后运行 `codex plugin remove gemini@personal`，移除旧安装项。Codex 将两个来源视为独立安装，同时启用会出现重复的 skill。

## 在 Codex 中使用

可以选择对应 skill，也可以直接说：

- "用 Gemini 审查当前未提交的改动。"
- "用 Gemini 审查这个分支相对 main 的改动。"
- "让 Gemini 根据原始需求审查这份实施计划。"

| Skill | 用途 |
| --- | --- |
| `gemini:review` | 审查工作区改动、分支或提交，可指定关注点 |
| `gemini:audit` | 根据目标审查一份或多份计划、规格文档 |
| `gemini:setup` | 检查 CLI、模型访问和 macOS 写入保护 |
| `gemini:status` | 查询后台任务进度 |
| `gemini:result` | 读取报告并核实发现的问题 |
| `gemini:cancel` | 取消正在运行的任务 |

默认模型为 `gemini-3.8-flash-high`，也可明确指定 `gemini-3.8-flash-medium` 或 `gemini-3.1-pro-high`。插件不会自动切换模型。

当前审查提示词要求模型用中文输出报告。

## 命令行用法

在 `plugins/gemini/` 目录中运行：

```bash
python3 scripts/gemini.py setup
python3 scripts/gemini.py review --repo /path/to/project
python3 scripts/gemini.py review --repo /path/to/project --base main --background
python3 scripts/gemini.py review --repo /path/to/project --commit HEAD --focus '检查竞态和数据丢失'
python3 scripts/gemini.py review --repo /path/to/project --model gemini-3.1-pro-high
python3 scripts/gemini.py audit /path/to/plan.md /path/to/spec.md --repo /path/to/project --goal '原始需求'
python3 scripts/gemini.py status JOB_ID --repo /path/to/project
python3 scripts/gemini.py result JOB_ID --repo /path/to/project
python3 scripts/gemini.py cancel JOB_ID --repo /path/to/project
```

### 审查范围

- 不带 `--base` 或 `--commit` 时，审查工作区相对 `HEAD` 的改动，包括暂存、未暂存，以及未被 Git 忽略的未跟踪文件。
- `--base REF` 审查当前分支与 `REF` 的共同祖先（merge base）到 `HEAD` 之间的改动。
- `--commit REF` 审查指定提交相对其第一父提交的改动；根提交则与空目录树比较。
- 分支和提交审查不包含工作区中尚未提交的改动。
- 可重复使用 `--path` 限定文件范围。

`--background` 会返回任务 ID，审查在后台继续。`--timeout` 设置每次模型调用的超时时间，单位为秒，默认值为 600。

## 审查流程

每次任务先保存输入快照并记录内容哈希值。总量在 18,000 字符预算内的多文件变更合并审查；较大变更按路径排序，能唯一匹配的实现文件与测试文件在预算允许时放入同一批。这是基于文件名的分组规则，不包含依赖分析。大文件拆分时保留文件标记和原始行号；单个超长行保留完整内容，可能超过预算。模型的 token 上限另行计算。

正常的批次审查共用一个持续运行的 Antigravity 进程和会话，后续轮次可复用已经读取的源码上下文。单批审查会同时检查本批内的跨文件问题；存在多个批次或恢复性拆分时，再沿用会话检查批次间的相互影响，仅在证据不足时读取原始快照。快照仍受写入保护。

若回复超过输出 token 上限，插件会关闭失败会话，并使用准确的会话 ID 请求简短续写；若续写仍然超限，会在限定的重试次数内进一步拆分满足条件的批次，并使用新的会话审查。

每次模型回复最多包含 8 条发现。如果仍有已确认的问题未能报告，模型必须标记该回复不完整。最终报告合并各批次的发现，不受总计 8 条的限制。Codex 随后根据源代码核实证据、位置和修正建议。

### 结果与诊断

任务文件保存在 `~/.cache/gemini-codex/jobs/<job-id>/`。每个目录包含输入快照、原始响应流、错误信息、模型用量，以及 JSON 和 Markdown 格式的最终报告。每轮调用关联对应的持续会话。标准错误日志按会话保存，因为延迟到达的诊断无法可靠归属某一轮。服务端用量可能按会话累计，插件保留原值，不把各轮累计值相加。目录仅允许当前用户访问。这些文件可能包含项目代码，不会上传到插件市场。可通过 `GEMINI_PLUGIN_STATE` 指定其他任务目录，例如用于测试。

`completed` 表示所有必要批次及汇总检查都返回了有效、完整的报告，并不证明代码没有问题。`partial`、`failed`、`cancelled` 和 `interrupted` 均表示审查未完成。即使 CLI 进程退出码为 `0`，只要模型返回的状态为 `ERROR`，仍会被视为失败。

## 权限与限制

插件通过 macOS `sandbox-exec` 禁止审查进程写入项目、Git 元数据、输入快照和明确选定的文档，同时启用 Antigravity 的 terminal sandbox，并通过提示词限定相关源码的只读工具。项目写入保护由操作系统执行。Antigravity 仍可在自己的目录中写入登录信息和会话数据。插件不会修改全局权限设置，也不会开启全部工具自动批准。

审查材料会通过已登录的 Antigravity 账号发送给 Google。Gemini 可以读取指定仓库中与审查相关的源码上下文。插件会明确将项目目录和快照目录登记为工作区路径。

保存输入时，插件会排除凭据类路径、二进制文件、子模块和未跟踪的符号链接，并记录这些遗漏。提示词也禁止读取已识别的凭据路径。插件不会对任意源码内容进行密钥扫描。如果 Antigravity 拒绝某项操作，任务会记录权限失败并停止后续调用。

审查分支或历史提交时，如果当前工作区与目标版本不同，仍以捕获的改动作为审查证据。缺少历史上下文的情况会列入报告限制。

输出格式约束无法消除模型内部推理的 token 上限。重试次数有限，持续失败时会保留已完成部分和诊断信息。分批审查与汇总检查仍可能漏掉跨文件问题。

实测结果及其适用范围见[性能验证记录](docs/performance.md)。

## 开发与测试

插件源码位于 `plugins/gemini/`，Codex 插件市场清单位于 `.agents/plugins/marketplace.json`。

在仓库根目录运行离线测试：

```bash
python3 -m unittest discover -s plugins/gemini/tests -v
```

这些测试不需要 Gemini 登录，也不会请求模型 API。

## 参考与致谢

设计参考 [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc)，版本为 `1.0.6`，提交为 `db52e28f4d9ded852ab3942cea316258ae4ef346`。借鉴的部分包括任务持久化、结构化结果，以及由主模型核实审查发现的分工。本项目独立实现了 Antigravity 运行层。

参考插件的常规审查使用 Codex app-server 的原生 `review/start` 接口。本插件通过 Antigravity 的通用 agent 接口组织审查，不复现原生 Codex reviewer 的内部流程。

本项目由个人独立维护，与 OpenAI 或 Google 无官方隶属关系。

接口参考：[Antigravity headless mode](https://antigravity.google/docs/cli/headless/) 和 [permissions](https://antigravity.google/docs/permissions/)。

## 许可证

采用 Apache-2.0，详见 `LICENSE` 和 `NOTICE`。
