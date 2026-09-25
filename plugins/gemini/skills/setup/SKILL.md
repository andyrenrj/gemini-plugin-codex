---
name: setup
description: Check the Gemini Codex plugin's Antigravity CLI, model access and macOS repository write protection.
---

# Gemini setup

Resolve `<plugin-root>` as two directories above this skill directory and run:

```bash
python3 "<plugin-root>/scripts/gemini.py" setup
```

This calls `agy models` using cached credentials. It does not install tools or alter account settings. If `agy` is missing, use the official [installation documentation](https://antigravity.google/docs/cli/installation/). If unauthenticated, the user needs to complete the interactive `agy` login in a terminal. Do not request tokens, export credentials, or switch to an API key.

The first release requires macOS `sandbox-exec` to deny project writes. If the Codex host sandbox blocks localhost binding or Antigravity metadata writes, use the host's normal permission mechanism for the requested review. Do not bypass an approval rejection.
