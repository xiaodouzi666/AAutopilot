# Risk Register and Fallback Ladder

## Principle

A complete, honest A0–A3 submission with a strong report is better than an unfinished attempt at every optional feature. Use the highest fallback that preserves the central Arm-specific optimization story.

## Risk matrix

| Risk | Likelihood | Impact | Early signal | Mitigation / fallback |
|---|---|---|---|---|
| No suitable Arm64 Linux target | Medium | Critical | `uname -m` not Arm; no SSH target | Provision any official-category Arm CPU host; development may continue with mocks, but final claims wait for Arm |
| KleidiAI build failure | Medium | Critical | CMake option/dependency error | Verify current official build docs; pin compatible `llama.cpp`; use GCC/Clang supported by target; keep generic build working while fixing |
| KleidiAI marker absent | Medium | Critical | Startup log lacks `CPU_KLEIDIAI` | Confirm optimized binary path, model operation support, CPU backend selection, `--device none`; reject false optimized run |
| Model download slow/unavailable | Medium | High | HF timeout/disk error | Resume download; use minimum weak Q4_0, strong Q4_0, strong Q8_0 set; do not mirror model artifacts |
| Full search exceeds deadline | High | High | Candidate ETA too long | Enforce staged top-k search, quick mode, runtime budget, cache/resume; prioritize fair A1/A2 and A3 |
| Weak model produces poor JSON | High | Medium | schema failures | Constrained JSON/grammar; one retry; automatic escalation; reject cascade if needed |
| Cascade fails held-out quality | Medium | Low | quality drop > gate | Ship tuned strong-only A3; present rejection as evidence of honest quality-aware optimization |
| Two resident models use too much RAM | Medium | Medium | OOM/high RSS | Sequential loading mode, lower context, Q4 models, or strong-only profile; report tradeoff |
| `llama.cpp` CLI flags changed | Medium | Medium | unknown-option errors | Parse pinned binary `--help`; map concepts through version adapter; store interface snapshot |
| Thread defaults misleading | High | Medium | inconsistent microbench | Always pass explicit thread values and sweep host-derived candidates |
| Cloud noise destabilizes results | Medium | High | CV >10% | More repetitions, paired/randomized order, dedicated host, stable affinity, disclose variance |
| `perf` denied | High | Low | permission/counter error | Save reason; continue timing/RSS; use Performix if configured |
| Performix/MCP unavailable | Medium | Low | missing tool/config | Mark optional integration unavailable; use `perf`; never block core |
| Devpost video cannot be finished | Medium | Medium | recording/upload delay | Submit complete write-up, screenshots, report, and repo first; video is optional |
| Public repo accidentally leaks secrets | Medium | Critical | secret scan hit | Redaction, `.gitignore`, grep/secret scanner, inspect history before push |
| Numeric placeholders remain | Medium | High | template token grep | Make final renderer fail hard on placeholders; submit no unmeasured claim |
| License ambiguity | Low | High | missing metadata | Use official Qwen repos and pinned runtime; Apache-2.0 project license; third-party notices |
| RK3588 integration consumes time | High | Medium | board-specific build issues | Skip until main cloud artifacts complete; label as optional portability proof |

## Fallback ladder

### Level 0 — Full target

- A0–A4 complete;
- quality-gated cascade passes;
- Performix evidence included;
- optional RK3588 portability result;
- video and screenshots complete.

### Level 1 — Strong championship submission

- A0–A4 complete;
- cascade passes;
- no Performix or RK3588;
- full report, video, public repo.

### Level 2 — Core technical submission

- A0–A3 complete;
- cascade rejected or omitted honestly;
- fair KleidiAI and tuning evidence;
- report, API demo, public repo, screenshots/video if possible.

### Level 3 — Minimum valid optimization submission

- generic Q4_0 versus KleidiAI Q4_0 fair comparison;
- objective incident demo;
- raw data and reproducible one-command report;
- public licensed repository and English write-up.

Do not fall below Level 3. A project that merely runs an LLM on Arm is not sufficient for this challenge.

## Deadline guardrails

- Create/save a Devpost draft before deep optional work.
- Freeze the `llama.cpp` commit once both builds work.
- Freeze the final benchmark target before final measurements.
- Stop adding features when fewer than four hours remain.
- Reserve the final two hours for public-repo validation, screenshots/video upload, Devpost fields, and submission receipt verification.
