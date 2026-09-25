---
name: review
description: Run an independent Gemini code review when the user asks for Gemini or an Antigravity second opinion on a diff, commit, or branch. Uses the existing Antigravity subscription.
---

# Gemini review

Resolve `<plugin-root>` as two directories above this skill directory. Invoke its `scripts/gemini.py` by absolute path. Python 3.10+, macOS and an authenticated `agy` CLI are required; use the bundled setup skill if unavailable.

Choose exactly the requested target:

- Default: `review --repo "<project>"` captures staged, unstaged and non-ignored untracked files against HEAD.
- Branch: add `--base "<ref>"`. This reviews merge-base to HEAD, excluding working-tree edits.
- Commit: add `--commit "<ref>"`. This compares to the first parent (empty tree for a root commit).
- Specific files: add repeatable `--path "<repo-relative-path>"`.
- Directed or adversarial review: add `--focus "<user's concern>"`; preserve the user's emphasis.

Example:

```bash
python3 "<plugin-root>/scripts/gemini.py" review --repo "<project>" --base main --background
```

The runtime captures inputs, combines small related files within an 18,000-character budget, and splits large files while preserving source line numbers. Normal batches share one persistent conversation. A single batch includes cross-file checks; multiple batches and recovery splits also receive an integration pass. The runtime protects the project from writes and saves raw responses. The batching budget is a heuristic, not a model limit; oversized single lines remain intact. Do not manually launch a separate review per file. Credential-like paths, binaries and submodules are explicitly omitted; check the reported omissions.

Use `--background` when the user asks for background work. Otherwise wait for completion; a shell tool may yield a process session that you should resume. Do not launch duplicate reviews while waiting. For a background job, report the job ID and use the bundled status/result/cancel skills to manage it.

Default model: `gemini-3.8-flash-high`. Explicit alternatives: `--model gemini-3.8-flash-medium` or `--model gemini-3.1-pro-high`. Never change model silently. A timeout is per model attempt, configurable with `--timeout <seconds>`.

On completion read [result handling](../result/SKILL.md). Report failures and incomplete coverage honestly, including `exit=0` with `status=ERROR`. Gemini findings are review suggestions; fix code only when the user's existing request also authorizes fixes.
