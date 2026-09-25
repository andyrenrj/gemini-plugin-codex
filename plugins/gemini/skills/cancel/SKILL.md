---
name: cancel
description: Cancel a running Gemini review or document audit job when requested by the user.
---

# Cancel Gemini job

Resolve `<plugin-root>` as two directories above this skill directory. Run:

```bash
python3 "<plugin-root>/scripts/gemini.py" cancel "<job-id>" --repo "<project>"
```

The worker receives a cancellation marker and stops its own Antigravity process group. Check status to confirm cancellation. Omitting the job ID is accepted only when exactly one job is active in this project. Keep partial artifacts available and do not present them as a completed review.
