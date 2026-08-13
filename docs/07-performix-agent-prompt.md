# Optional Arm Performix MCP Agent Prompt

Use this only when the Arm MCP Server and an Arm Performix-capable remote Linux target are already configured. Core project success must not depend on it.

## Objective

Collect structured, reproducible hotspot evidence for representative generic and KleidiAI `llama-bench` runs, compare the profiles, and propose only project-owned orchestration/runtime changes. Do not modify `llama.cpp` or KleidiAI source under the deadline unless a tiny, clearly justified fix is required.

## Prompt for Claude Code / Codex

```text
You are profiling AArch64 Autopilot on its final Arm64 Linux target using the Arm MCP Server and Performix.

Inputs to discover from the repository:
- redacted SSH target configuration already approved for MCP use;
- generic llama-bench binary path;
- KleidiAI llama-bench binary path;
- selected Q4_0 strong-model path;
- exact representative command line from the fair A1/A2 benchmark pair;
- artifacts directory.

Rules:
1. Never expose SSH credentials, hostnames, usernames, or public IPs in committed output.
2. Use the same model, workload, thread count, affinity, and other parameters for generic and KleidiAI profiles.
3. Call arm-mcp/apx_recipe_run with the Code Hotspots recipe for each binary/command in separate runs.
4. Save the structured summaries to:
   artifacts/performix/generic-hotspots.json
   artifacts/performix/kleidiai-hotspots.json
5. Record tool/recipe version and redacted target identity.
6. Compare top CPU-time-consuming functions and call-stack context. Do not infer a speedup from sample percentages alone; use the normal benchmark for speed claims.
7. Inspect project-owned process management, HTTP client, RSS sampling, parsing, and routing code for avoidable overhead identified by the profile.
8. Make at most three targeted project-owned changes, one at a time. For each change:
   a. state the hypothesis;
   b. implement and test it;
   c. rerun the same benchmark and Performix recipe;
   d. keep the change only if the measured benchmark improves without failing quality, safety, or correctness.
9. Preserve before/after raw results and commit each retained change separately.
10. Generate artifacts/performix/summary.md with:
   - exact profiled commands;
   - top hotspots;
   - retained/rejected hypotheses;
   - benchmark deltas and source run IDs;
   - limitations.
11. Link the summary into the main report, but do not make Performix a setup requirement for judges.
```

## Fallback

When MCP/Performix is unavailable:

- write `artifacts/performix/UNAVAILABLE.md` with the exact non-secret reason;
- run the best-effort `perf stat`/`perf record` workflow;
- continue all mandatory build items.
