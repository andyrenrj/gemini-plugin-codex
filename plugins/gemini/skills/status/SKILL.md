---
name: status
description: Check a Gemini review or plan/spec audit job's recorded progress and whether its worker is still running.
---

# Gemini status

Resolve `<plugin-root>` as two directories above this skill directory. Run:

```bash
python3 "<plugin-root>/scripts/gemini.py" status "<job-id>" --repo "<project>"
```

Omit the job ID for this project's latest job. Present status, phase, elapsed context when available, and artifact directory. Do not claim a job is running merely because it was launched. `interrupted` means the stored worker is no longer present. Use the result skill for a terminal job. Avoid frequent polling; wait approximately 30 seconds between unchanged checks.
