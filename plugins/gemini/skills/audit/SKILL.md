---
name: audit
description: Ask Gemini to independently audit a plan or spec for contradictions, missing decisions, failure modes and verifiable acceptance criteria.
---

# Gemini document audit

Resolve `<plugin-root>` as two directories above this skill directory. Use the actual project directory and absolute UTF-8 document paths:

```bash
python3 "<plugin-root>/scripts/gemini.py" audit "<plan.md>" "<spec.md>" --repo "<project>" --kind plan --goal "<user's original objective>"
```

Use `--kind spec` for a specification audit. Include related documents explicitly when needed to judge consistency. Preserve the user's goal and uncertainty; do not add invented requirements. `--focus` supports an adversarial challenge or a specific concern. `--background`, `--model`, and `--timeout` work as in code review.

The runtime captures and numbers the document text, combines small documents within a bounded batch, and reuses a persistent conversation across normal batches. It checks interactions within each batch and adds an integration pass when multiple batches or recovery splits are needed. Large-document splitting can reduce context; present the model's limitations and inspect the combined report. Read [result handling](../result/SKILL.md) before reporting. Audit does not authorize rewriting the document unless the user also requested revisions.
