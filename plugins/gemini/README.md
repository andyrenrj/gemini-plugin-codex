# Gemini Review for Codex

A Codex plugin for independent Gemini reviews of code changes, implementation plans, and specifications. It runs through the Antigravity CLI using your existing sign-in and subscription.

## Requirements

- macOS with `sandbox-exec` for project write protection
- Python 3.10 or later and Git
- A Codex CLI version that supports plugins
- An installed, signed-in [Antigravity CLI](https://antigravity.google/docs/cli/installation/) (`agy`)

You do not need a Gemini API key. Model access and usage limits depend on your Antigravity account.

## Installation

```bash
codex plugin marketplace add andyrenrj/gemini-plugin-codex
codex plugin add gemini@gemini-review
```

Start a new Codex task after installation. Run `gemini:setup` to check the environment, then use `gemini:review` or `gemini:audit`.

If you are switching from the local development copy, run `codex plugin remove gemini@personal` after installing the GitHub version. Codex treats the two sources as separate installations, so keeping both enabled creates duplicate skills.

## Usage in Codex

Select a skill or ask Codex directly, for example:

- "Use Gemini to review my uncommitted changes."
- "Use Gemini to review this branch against main."
- "Ask Gemini to audit this implementation plan against the original requirements."

| Skill | Purpose |
| --- | --- |
| `gemini:review` | Review working-tree changes, a branch, or a commit, with an optional focus |
| `gemini:audit` | Audit one or more plans or specifications against a stated goal |
| `gemini:setup` | Check CLI availability, model access, and the macOS write guard |
| `gemini:status` | Check a background job's progress |
| `gemini:result` | Retrieve a report and verify its findings |
| `gemini:cancel` | Cancel a running job |

The default model is `gemini-3.8-flash-high`. You can explicitly select `gemini-3.8-flash-medium` or `gemini-3.1-pro-high`. The plugin never switches models automatically.

The current review prompt requests reports in Chinese.

## Command-line usage

Run these commands from this plugin directory:

```bash
python3 scripts/gemini.py setup
python3 scripts/gemini.py review --repo /path/to/project
python3 scripts/gemini.py review --repo /path/to/project --base main --background
python3 scripts/gemini.py review --repo /path/to/project --commit HEAD --focus 'Check for race conditions and data loss'
python3 scripts/gemini.py review --repo /path/to/project --model gemini-3.1-pro-high
python3 scripts/gemini.py audit /path/to/plan.md /path/to/spec.md --repo /path/to/project --goal 'The original requirements'
python3 scripts/gemini.py status JOB_ID --repo /path/to/project
python3 scripts/gemini.py result JOB_ID --repo /path/to/project
python3 scripts/gemini.py cancel JOB_ID --repo /path/to/project
```

### Review scope

- Without `--base` or `--commit`, a review compares the working tree with `HEAD`, including staged, unstaged, and non-ignored untracked files.
- `--base REF` reviews changes from the merge base with `REF` to `HEAD`.
- `--commit REF` reviews the specified commit against its first parent, or an empty tree for a root commit.
- Branch and commit reviews exclude uncommitted working-tree changes.
- Repeat `--path` to restrict the reviewed files.

`--background` returns a job ID while the review continues. `--timeout` sets the limit for each model attempt in seconds; the default is 600.

## How reviews work

Each job captures its input and records a content hash. The runtime splits large files into batches of roughly 18,000 characters, preserving source line numbers. This threshold controls input batching; model token limits are separate.

After successful batch reviews, an integration pass checks interactions across batches and files. It can read the original snapshots, which are protected against writes. If a response exceeds the output token limit, the runtime first requests a shorter continuation using the exact conversation ID. If that also exceeds the limit, eligible batches are split further within a bounded retry budget.

Each model response contains at most eight findings. If that would leave confirmed issues unreported, the model must mark the response incomplete. The final report combines findings across batches without imposing an eight-finding total. Codex then checks the evidence, locations, and suggested fixes against the source.

### Results and diagnostics

Jobs are stored in `~/.cache/gemini-codex/jobs/<job-id>/`. Each directory contains the captured input, raw response streams, errors, model usage, and final JSON and Markdown reports. Access to the job directory is restricted to the current user. These files may contain project code and are not uploaded to the plugin marketplace. Set `GEMINI_PLUGIN_STATE` to use a different job directory, such as for testing.

`completed` means all required batches and the integration pass returned valid, complete reports. It does not prove that the code is correct. `partial`, `failed`, `cancelled`, and `interrupted` indicate incomplete reviews. A CLI process that exits with code `0` but returns model status `ERROR` is treated as a failure.

## Permissions and limitations

The runtime uses macOS `sandbox-exec` to deny writes to the project, Git metadata, review snapshots, and explicitly selected documents. It also runs Antigravity in plan mode with terminal sandboxing. Antigravity can still write its own authentication and session data. The plugin does not change global permission settings or enable blanket tool approval.

Review material is sent to Google through your signed-in Antigravity account. Gemini can read relevant source context in the selected repository. The runtime explicitly registers the project and snapshot directories as workspace paths.

Input capture excludes credential-like paths, binary files, submodules, and untracked symlinks, and records those omissions. The prompt also prohibits reading the identified credential paths. The plugin does not scan arbitrary source content for secrets. If Antigravity denies an action, the job records the permission failure and stops further calls.

For branch and commit reviews, captured changes remain the authoritative evidence when the current working tree differs. Missing historical context is reported as a limitation.

Output constraints cannot eliminate the model's internal reasoning token limits. Retries are bounded; persistent failures retain completed work and diagnostics. Splitting and integration checks can still miss issues across files.

## Development

In the GitHub repository, the plugin lives in `plugins/gemini/` and the Codex marketplace manifest is `.agents/plugins/marketplace.json`.

Run the offline tests from this plugin directory:

```bash
python3 -m unittest discover -s tests -v
```

These tests do not require a Gemini login or make model API requests.

## Acknowledgments

The design draws on [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc), version `1.0.6`, commit `db52e28f4d9ded852ab3942cea316258ae4ef346`: persistent jobs, structured results, and verification by the host model. This project implements its own Antigravity runtime.

The reference plugin's standard review uses the Codex app-server's native `review/start` interface. This plugin organizes reviews through Antigravity's general agent interface; it does not reproduce the native Codex reviewer's internal workflow.

This is an independently maintained project with no official affiliation with OpenAI or Google.

Protocol references: [Antigravity headless mode](https://antigravity.google/docs/cli/headless/) and [permissions](https://antigravity.google/docs/permissions/).

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
