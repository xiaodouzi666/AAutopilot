# Benchmark and Evidence Protocol

This protocol exists to prevent a fast-looking but scientifically weak submission. The agent must follow it unless a target limitation is recorded explicitly.

## 1. Questions the experiment must answer

1. Does the same Q4_0 model run faster with KleidiAI enabled than with a generic `llama.cpp` CPU build on the same Arm64 machine?
2. How much do quantization, KleidiAI, runtime tuning, and model cascading each contribute?
3. Does the final configuration preserve objective task quality and 100% safety on held-out cases?
4. Is the selected profile stable, reproducible, CPU-only, and deployable through an API?
5. What tradeoffs remain between latency, throughput, memory, and weak-model usage?

## 2. Experimental invariants

For any direct backend comparison, hold constant:

- physical/virtual Arm host;
- OS and kernel;
- `llama.cpp` source commit;
- compiler and build type;
- model file and checksum;
- prompt/case and output limit;
- sampling parameters;
- CPU affinity;
- thread/batch/context/parallel settings;
- process startup policy;
- warmup and repetition count.

The intended build difference is only KleidiAI on versus off.

## 3. Machine preparation

Before final runs:

1. Stop unrelated high-load workloads when possible.
2. Record load average and free memory.
3. Record CPU frequency governor and whether it can be controlled.
4. Record cgroup/container CPU and memory limits.
5. Record NUMA topology and process affinity.
6. Synchronize clocks only for metadata; use monotonic clocks for measurements.
7. Do not clear OS caches unless the protocol clearly distinguishes cold and warm runs.
8. Run one compatibility warmup for each binary/model pair.

If the target is thermally constrained, add a fixed cooldown and record temperature where available. Do not silently compare hot and cold candidates.

## 4. Sampling controls

Use deterministic generation settings for quality comparisons:

```text
temperature = 0.0
top_p = 1.0
seed = fixed if supported
max output tokens = 512
context = fixed, initially 2048 or 4096
```

Use the same system prompt, JSON constraint, and stop conditions across candidates.

## 5. Test stages

### 5.1 Smoke stage

Purpose: reject broken candidates cheaply.

- one short prompt;
- one incident case;
- one measured request after warmup;
- schema check;
- backend proof;
- CPU-only proof.

Failure means the candidate is excluded, with error logs retained.

### 5.2 Microbenchmark stage

Use the pinned `llama-bench` interface, discovered from `--help`, to capture short prompt-processing and token-generation tests. Do not rely on default thread count; explicitly test thread candidates because defaults can be unsuitable on some machines.

For each combination:

- 1 warmup;
- at least 3 measured repetitions;
- model, quant, backend, thread/affinity recorded;
- raw stdout/stderr saved;
- parse only values that match a versioned parser test.

The minimum bounded matrix is not a single preferred configuration. It contains generic Q8_0,
generic Q4_0, and KleidiAI Q4_0 at two topology-derived thread counts
(`max(1, physical_threads // 2)` and `physical_threads`, which must be distinct). Each invocation
runs both `pp128` and `tg64`, keeps the tool's one default warmup, and uses at least three measured
repetitions. Both binaries must discover and use explicit `--device none`, `-ngl 0`, and exactly
one canonical `-v` flag so the backend marker remains replayable;
a missing flag, parser row, runtime backend marker, raw log, or matrix cell invalidates the entire
supporting artifact. Generic/KleidiAI Q4_0 rows are paired at each thread count, while the generic
Q8_0/Q4_0 cells provide the minimum quantization ablation.

This stage is ranking evidence, not the final user-facing latency result.

The A3 tuner consumes this verified artifact rather than independently guessing thread counts.
For KleidiAI Q4_0, rank the complete micro cells by `tg64` tokens/s, then `pp128` tokens/s, then
thread count. Only these micro-measured thread counts proceed to bounded service calibration. The
search plan stores the ranking and a redaction-stable semantic probe digest; each private or
sanitized bundle still verifies its own raw-log hashes independently.

### 5.3 Service stage

Launch a fresh `llama-server` for each candidate unless testing steady-state concurrency. Measure via the OpenAI-compatible streaming API.

The bounded supporting service matrix uses the same strong Q4_0 checksum and frozen request
settings for generic and KleidiAI at `parallel=1` and `parallel=2`. Each cell uses a fresh server,
one unmeasured warmup round, and at least three measured rounds. The p2 server receives twice the
total context so each concurrent slot retains the same 2048-token capacity as p1. Streaming
requests explicitly set `stream_options.include_usage=true`; missing positive prompt/completion
usage or a missing first content token fails closed rather than producing zero/null token rates.
The supporting workload is one fixed calibration case and is never merged into held-out rows.

Every warmup and measured request retains a hashed request/response receipt. Each measured round
stores its enclosing monotonic start/end counters and the exact request IDs, repetitions, client
indices, and generated-token sum. Strict replay requires bidirectional membership, every request
interval to fall inside its round, and all stored rates/scores/tokens to reproduce from receipts.
Startup start/readiness counters, a post-readiness idle process-tree RSS sample, the finalized RSS
CSV peak, process command receipt, and distinct fresh-start ID are likewise mandatory.

Formal A3 calibration remains `parallel=1` because its quality runner sends requests sequentially.
It must not rank or deploy p2/p4 from single-request latency or label `median(1/E2E)` as aggregate
concurrency throughput. The independent p1/p2 probe rounds above are the protocol's concurrency
evidence and preserve equal context per slot.

For each request record:

- request start;
- first content token;
- response end;
- prompt/completion token counts;
- server timing fields where exposed;
- process RSS samples;
- route/escalation;
- schema/quality/safety result;
- errors and retries.

### 5.4 Calibration stage

Run only on the 40 calibration cases. Select:

- complexity threshold;
- candidate runtime profile;
- any retry/escalation policy.

Store all calibration results, but keep them visually separate from final held-out claims.

Every calibrated candidate requires an exact receipt over the frozen calibration prefix and all
requested repetitions. Resume reuses only a receipt whose cited run IDs replay to that exact raw
matrix and exact candidate settings. Partial or unreceipted raw data stops the search; it is not
silently rerun. The wall-clock deadline is an absolute monotonic deadline propagated into server
startup and each request, not merely a check between candidates.

The authoritative final split is `demo/split.json` schema version 2.0. It was frozen before
the next final run using the following preregistered procedure:

1. Treat the 20 test cases from split v1 and the four v1 calibration cases executed during
   the failed run (`incident-001`, `incident-002`, `incident-004`, and `incident-005`) as
   observed. This is a 24-case exclusion set.
2. Use only the remaining 36 v1 calibration cases as candidates for the new final test set.
3. For every candidate, compute the lowercase hexadecimal digest
   `sha256(f"{domain}|{seed}|{case_id}")`, where
   `domain = "a64pilot-final-holdout-v2"` and `seed = 20260813`. Category is used only to
   stratify candidates; it is not part of the digest input.
4. Sort each category by digest ascending and select 6 `simple`, 7 `multi`, 3 `noisy`, and
   4 `ambiguous` cases. Store the selected union in global `(digest, case_id)` ascending order.
5. Form calibration from the 20 unselected v1 calibration cases in their old order (therefore
   preserving `001/002/004/005` as the first four), followed by all 20 v1 test cases in their
   old order.

The manifest must cover 60 unique IDs, contain exactly 40 calibration and 20 test IDs, have
the stated test-category quotas, and have zero overlap between the v2 test set and the 24-case
observed exclusion set. A test enforces the procedure rather than only checking the final
counts. The immutable audit record `demo/split-freeze-v2.json` stores both split hashes, the
observed and eligible pools, every candidate digest and category, and the selected order.
Selection consumed only case ID and category: it did not consume expected answers, tool or
safety labels, model outputs, quality scores, latency, or any other run result.

### 5.5 Held-out stage

Freeze all decisions before running the 20 held-out cases. Evaluate the five ablation stages with at least three repetitions per case where time permits. A minimum reduced run may use one quality repetition plus repeated performance probes, but the report must disclose this.

For the deadline-safe reduced run, A0, A1, and A2 still cover all 20 held-out cases once; quick
mode may reduce only calibration search depth, never those formal ablations or admitted A3
finalists. The report states the exact formal quality repetitions per case and separately labels
the three-or-more-round micro/service probes as supporting ranking/concurrency evidence. Probe
rows cannot increase the formal held-out sample count or support the preregistered headline claim.

The official reduced workflow composes explicit phase budgets: 20 minutes for supporting probes,
35 minutes for the complete A0–A2 formal phase, 45 minutes for A3 search, and 30 minutes each for
A4 calibration and frozen-policy replay. Each phase passes one absolute monotonic deadline through
capability discovery, server startup, warmup, and every request; the 240-minute Actions job timeout
is only an outer safety margin, not the primary stopping mechanism. An incomplete phase fails
closed and cannot emit an admitted candidate or publication artifact.

Do not change thresholds after viewing held-out labels. Freeze the ordered
`scheduled_finalists` before this phase. Write a candidate to `admitted_finalists` only after its
entire 20-case by repetition matrix completes and its raw-derived gate receipt is stored. Any
timeout or execution failure leaves it unadmitted and fails the tuning command closed.

The v1 test set was executed in failed Arm run `31758292648` (`run6`) and then used for error
analysis, so it is calibration evidence and must never again be described as unseen or final
held-out evidence. Split v2 was frozen after that analysis and before its first formal use in
published run `31778419786`. The upcoming run keeps split v2 and the preregistered primary
definition unchanged, but it is a replication/revalidation on a previously executed test split,
not a newly unseen holdout. Reports must disclose that history and must not use held-out outcomes
to reorder the calibration-frozen finalists.

## 6. Baselines and ablations

### A0 — Reference precision baseline

- strong 1.5B model;
- Q8_0;
- generic CPU build;
- fixed reasonable runtime settings;
- all requests strong model.

Purpose: model-size/quantization reference.

### A1 — Fair generic baseline

- strong 1.5B model;
- Q4_0 (the quantized format supported by the pinned KleidiAI Q4 kernel path);
- generic CPU build;
- same fixed settings;
- all requests strong model.

Purpose: apples-to-apples Arm backend baseline.

### A2 — KleidiAI only

- same Q4_0 model;
- KleidiAI build;
- same fixed settings and affinity;
- all requests strong model.

Purpose: isolate the Arm-specific backend contribution.

### A3 — Runtime tuned

- same strong Q4_0 model;
- KleidiAI build;
- selected threads, batch, micro-batch, concurrency, context, and affinity;
- all requests strong model.

Purpose: isolate device-aware runtime tuning.

### A4 — Full AArch64 Autopilot

- tuned KleidiAI profiles;
- weak 0.5B + strong 1.5B quality-gated cascade;
- automatic escalation;
- selected threshold frozen before held-out evaluation.

Purpose: demonstrate system-level agent inference optimization.

If A4 fails the quality gate, A3 becomes the shipping profile and the failed A4 remains a transparent experiment.

## 7. Quality and safety calculations

### Per-case score

```text
quality = schema(15) + diagnosis/severity(30) + tool selection(35) + safety(20)
```

Possible partial credit must be deterministic and documented. For example:

- exact diagnosis: full points;
- allowed equivalent diagnosis: partial points only if listed in the case;
- required tool recall and prohibited tool penalty computed from sets;
- any prohibited destructive action sets safety to zero for that case.

### Aggregate feasibility

A final candidate is feasible only when:

```text
safety_score == 100.0
schema_failure_count == 0
quality_score >= baseline_quality_score - max_absolute_quality_drop
p95 <= configured SLA, if supplied
peak_RSS <= configured limit, if supplied
```

The default maximum quality drop is 1.0 absolute point, not 1% relative.

## 8. Performance calculations

### Timing

```text
TTFT = first_content_token_monotonic - request_start_monotonic
E2E = response_end_monotonic - request_start_monotonic
decode_time = response_end_monotonic - first_content_token_monotonic
generation_tok_s = completion_tokens / decode_time
```

For non-streaming fallbacks, TTFT must be marked unavailable, not approximated.

### Throughput

Run bounded concurrent clients matching tested server parallel slots. Report:

- completed requests per second;
- generated tokens per second across all clients;
- p50/p95 request latency;
- error rate.

For every measured round, compute completed requests/s and aggregate generated tokens/s from one
shared monotonic wall interval. Also retain each request's TTFT, E2E, completion-token count, and
`completion_tokens / (response_end - first_content_token)` generation rate. The p1/p2 report shows
round mean and standard deviation plus request-level p50/p95 latency and fresh startup time.

`a64pilot verify-probes` reparses raw micro tables, service receipts, process/RSS evidence, CPU-only
configuration, and generic/KleidiAI markers; it binds binary/model hashes and inventories to the
build/model manifests. After redaction normalizes native elapsed prefixes and refreshes raw hashes,
the sanitized bundle must pass the same semantic replay in manifest-only public mode before it is
packaged. The private on-target gate additionally rehashes current binaries and model files.

Do not compare concurrency results at different quality/output limits without labeling them.

### Memory

Sample RSS for the full process tree. Report:

- idle resident memory after model load;
- peak RSS during requests;
- combined peak for both weak and strong servers in cascade mode, or explicitly `not measured / not
  eligible` when only component quality replay exists;
- model file bytes separately.

### Relative change

For latency, where lower is better:

```text
reduction_pct = (baseline - optimized) / baseline * 100
speedup_x = baseline / optimized
```

For throughput, where higher is better:

```text
increase_pct = (optimized - baseline) / baseline * 100
speedup_x = optimized / baseline
```

Do not use “X% faster” ambiguously; label the exact metric.

## 9. Statistical reporting

- Pair requests by case and repetition.
- Report median and p95 for latency.
- Report mean and standard deviation for token rates where conventional.
- For the complete A1 generic-Q4_0/A2 KleidiAI-Q4_0 pair, preserve mean time-to-first-token
  reduction as the single primary metric prospectively preregistered before split v2's first
  formal run. Unchanged-split replications must disclose its prior execution. Also retain p95
  end-to-end latency reduction and median per-request throughput increase
  (where per-request throughput is `1000 / E2E_ms`) as transparent secondary metrics.
- Compute a paired 95% bootstrap confidence interval for each of those three metrics using
  the same complete 20-case paired rows, 5,000 resamples, and seed `20260813`. Use the mean,
  p95, and median reducer respectively.
- Publish headline claims only when both A1 and A2 cover every required v2 test
  case/repetition, every row is schema-valid, both aggregate safety scores are 100%, and A2
  mean quality is no more than 1.0 absolute point below A1.
- Once the pair is eligible, report all three preregistered metrics, including negative values
  or intervals crossing zero; do not select only the most favorable metric. Only a positive
  primary mean-TTFT reduction whose 95% paired interval lower bound is greater than zero may
  unlock publication of a demonstrated-improvement result. Secondary p95 E2E and throughput
  results are always displayed but can never unlock publication on their own, even if positive.
  This decision rule is fixed before executing split v2 to prevent multiple-comparison
  cherry-picking.
- Show coefficient of variation.
- Flag rather than hide unstable runs.
- Include sample count next to every chart/table.

A result with a confidence interval crossing zero may still be reported as “no demonstrated improvement,” never as a win.

## 10. Provenance

Every full run receives a `run_id` and directory containing:

```text
artifacts/raw/<run_id>/
├── run-config.yaml
├── system-info.json
├── build-manifest.json
├── model-manifest.json
├── commands.jsonl
├── requests.jsonl
├── rss.csv
├── server-logs/
├── perf/
└── integrity.json
```

`integrity.json` contains hashes for all raw files. Summary generation must verify these hashes before rendering claims.

## 11. CPU-only verification

A final candidate passes CPU-only proof only if:

- build flags do not enable a GPU backend required for inference;
- runtime flags explicitly select CPU/no device where supported;
- startup log identifies CPU backend and no GPU offload;
- GPU layer count is zero where reported;
- the report stores the relevant log excerpt and command;
- an automated check marks `cpu_only_verified: true`.

A missing GPU on the machine is not, by itself, enough proof.

## 12. KleidiAI verification

A final KleidiAI candidate passes only if:

- build manifest includes `GGML_CPU_KLEIDIAI=ON`;
- startup output contains the pinned version’s KleidiAI buffer/backend marker;
- startup output contains the matching primary Q4 kernel marker;
- the exact pinned strong-Q4_0 inventory may report its one reviewed Q6_K `output.weight`
  non-accelerated fallback; any inventory drift, missing primary Q4 kernel, or additional
  unsupported/non-accelerated fallback fails;
- the generic binary lacks that marker;
- binaries and CMake caches are separately hashed;
- the same model checksum is used in the fair comparison.

## 13. Reporting limitations

The final report must explicitly state:

- results apply to the named target and software versions;
- small synthetic agent tasks are not a general LLM capability benchmark;
- model routing is calibrated for the included workload;
- power/energy is omitted unless measured by a credible available counter;
- cloud cost is only calculated when the user supplies an hourly instance price;
- no GPU or model training was used.

## 14. Final evidence checklist

Before rendering Devpost text, assert:

- a complete fair generic-Q4_0 versus KleidiAI-Q4_0 held-out pair exists;
- at least four complete ablation stages exist, or the report explains a failed stage;
- all headline candidates pass safety and schema gates;
- all claim values resolve to source run IDs;
- no calibration result is mislabeled held-out;
- no private target information remains;
- report regeneration from raw data succeeds.
