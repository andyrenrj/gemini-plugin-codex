# Review performance validation

## Small multi-file change

Tested on macOS with Antigravity CLI 1.2.10 and `gemini-3.8-flash-high` on September 25, 2026. The baseline was commit `e05a4aa`. The two runs used the same captured diff and ran sequentially, without another test review running at the same time.

The synthetic order service changed three Python files: `api.py`, `pricing.py`, and `store.py`. Two defects were introduced: the API returned cents in a dollar-denominated field, and order lookup stopped checking tenant ownership. Both defects were also reproduced by executing the fixture locally.

| Result | Baseline | Batching and session reuse |
| --- | ---: | ---: |
| Input batches | 3 | 1 |
| Model turns, including integration | 4 | 1 |
| Conversations | 4 | 1 |
| Wall time, including process startup and shutdown | 295.9 seconds | 29.5 seconds |
| Known defects reported | 2 of 2 | 2 of 2 |
| Review status | completed | completed |

The snapshot hash was identical in both runs: `5466f3e2eb8fec1ceb62d3d2bccd5222be340827fca398092b08b7f35d8d03db`.

This is one small synthetic sample. It demonstrates the benefit of avoiding separate reviews for small related files. It does not establish a general speedup, measure recall on unknown defects, or isolate process reuse from batching and prompt changes. Server load, caching, and model behavior can change timings. Token counters are provider diagnostics, not a measurement of subscription credits or billing.

## Persistent conversation

A separate protocol check assigned the same snapshot to two batches, then ran an integration pass. All three turns completed in one process and conversation, with one `init` event and three `result` events. Every turn included valid `structured_output`. Both known defects were reported.

The provider returned cumulative usage and turn counts. The runtime preserves them by session and attempt without summing cumulative values. Offline tests also cover output-limit recovery, incomplete reports, malformed or duplicate results, missing results at EOF, timeouts, and cancellation.

## Native subagents

A separate headless compatibility probe successfully launched two native reader subagents concurrently. Their invocation specified `Model: inherit` and `Workspace: inherit`; the temporary reader disabled write, MCP, and nested subagent tools. Both read the assigned fixture files and reached the idle state before the parent returned.

This directed probe asked about the two known defects explicitly, so it is not an independent defect-detection benchmark.

A cancellation probe terminated the parent process group immediately after dispatch. Resuming that exact parent conversation and querying `manage_subagents` twice showed both children idle, with no subsequent transcript growth. Cancellation happened before their first file read. This does not establish cancellation during active analysis or distinguish successful completion from cancellation: both can appear as idle.

Native parallel scheduling is not integrated in this release. The shipped runtime uses batching and a persistent sequential conversation. Adding native scheduling still requires validated per-batch results and cancellation during active child work; idle status alone must never count as completed coverage.
