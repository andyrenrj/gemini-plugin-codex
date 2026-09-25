---
name: result
description: Retrieve and verify a finished Gemini review job, preserving its evidence, failure status and coverage limitations.
---

# Gemini result

Resolve `<plugin-root>` as two directories above this skill directory. Run:

```bash
python3 "<plugin-root>/scripts/gemini.py" result "<job-id>" --repo "<project>"
```

Omit the job ID for this project's latest job. The artifact directory contains the captured `request.json`, progress `job.json`, raw attempt streams and diagnostics, and final `report.json`/`report.md`.

Present findings first, ordered P1 to P3. Check each finding against the captured target and relevant source. Clearly distinguish Gemini's finding from Codex's verification; do not silently replace a failed Gemini run with Codex's own review. If the project has changed since capture, say that this report covers the recorded snapshot, and check changed locations before proposing edits.

`completed` means the runtime obtained valid complete reports for its scope. It is not proof that the code is correct. `partial`, `failed`, `cancelled`, or `interrupted` must never be presented as approval or zero findings. Show omitted files and incomplete areas. Invalid JSON and output-limit failures retain diagnostic artifacts.

Do not treat findings as automatic authorization to edit. Respect any repair authorization already given in the user's request.
