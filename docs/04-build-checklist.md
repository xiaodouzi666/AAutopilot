# Autonomous Build Checklist

## Build mode

- **Mode:** autonomous, straight through to submission-ready MVP.
- **Human review pauses:** none required during implementation.
- **Git cadence:** one commit per numbered item after verification.
- **Fallback policy:** use documented fallback and continue; record it in `BUILD_STATUS.md`.
- **Hard stop:** genuinely unavailable Arm64 execution target or credentials that cannot be inferred from the environment.

## Time-boxed execution order

The remaining competition window is short. Complete a valid Devpost draft and repository shell early, then add measured evidence. Optional work must never delay submission.

### Target schedule from project start

| Elapsed | Milestone |
|---:|---|
| 0–1 h | Repository, license, status file, Devpost skeleton |
| 1–4 h | Arm doctor, pinned dual build, model acquisition |
| 4–8 h | Structured incident demo and proxy smoke test |
| 8–13 h | Benchmark engine and raw artifact store |
| 13–18 h | Staged tuner and quality-gated cascade |
| 18–23 h | Full Arm benchmark and ablations |
| 23–27 h | Report, README, tests, integrity checks |
| 27–31 h | Screenshots, video assets, submission copy |
| 31 h onward | Submission and contingency buffer |

---

- [ ] **1. Establish the submission-safe repository**

  **Spec ref:** `01-scope-and-prd.md > Non-functional requirements`; `00-competition-brief.md > Mandatory submission requirements`

  **What to build:**
  - Initialize Git repository and Python package.
  - Add Apache-2.0 `LICENSE`, `THIRD_PARTY_NOTICES.md`, `.gitignore`, `README.md` skeleton, `BUILD_STATUS.md`, `Makefile`, `pyproject.toml`, and directory tree.
  - Copy the competition deadline and selected category into the README.
  - Add a Devpost skeleton with nonnumeric placeholders controlled by templates.
  - Record whether the work is new or meaningfully updated during the challenge.
  - Run license/secret checks on every later submission build.

  **Acceptance:**
  - Root license is visible.
  - Package installs in a virtual environment.
  - `make help` lists all required commands.
  - No model binaries, credentials, build output, or private host data are tracked.

  **Verify:**
  ```bash
  git status --short
  python3 -m venv .venv
  .venv/bin/pip install -e '.[dev]'
  make help
  .venv/bin/python -m pytest -q
  ```

- [ ] **2. Implement Arm hardware doctor and provenance schemas**

  **Spec ref:** `02-technical-spec.md > Hardware doctor`

  **What to build:**
  - Typed system/build/model/run schemas.
  - `a64pilot doctor` and `make doctor`.
  - Architecture rejection for real benchmark on non-Arm.
  - CPU feature/topology detection and redaction.
  - Candidate affinity sets.
  - JSON and Markdown output with schema version.
  - Parser fixtures for representative Arm outputs.

  **Acceptance:**
  - On target, architecture is `aarch64`/`arm64`.
  - `artifacts/system-info.json` validates.
  - Relevant DotProd/I8MM/SVE/SME flags are evidence-backed, not guessed.
  - Hostname, username, IP, and home path are redacted in public copy.

  **Verify:**
  ```bash
  make doctor
  python -m a64pilot.cli doctor --json | jq .architecture
  pytest -q tests/test_hardware.py
  ```

- [ ] **3. Pin and build fair generic and KleidiAI runtimes**

  **Spec ref:** `02-technical-spec.md > Dual llama.cpp build`

  **What to build:**
  - Clone official `llama.cpp` into `third_party/`.
  - Select a current compatible commit, smoke test, then pin it.
  - Build generic and KleidiAI variants with otherwise identical flags.
  - Disable GPU backends where relevant.
  - Capture CMake cache, flags, compiler versions, source commit, binary hashes.
  - Implement backend and CPU-only verification parsers.

  **Acceptance:**
  - Both `llama-server`, `llama-cli`, and `llama-bench` binaries exist or their pinned equivalents are documented.
  - Generic build does not claim KleidiAI.
  - Optimized build produces the `CPU_KLEIDIAI` marker.
  - Runtime invocation explicitly disables GPU use.

  **Verify:**
  ```bash
  make build
  make verify-backends
  jq . artifacts/build-manifest.json
  diff -u artifacts/cmake-generic-flags.txt artifacts/cmake-kleidiai-flags.txt || true
  ```

- [ ] **4. Acquire and verify official GGUF models**

  **Spec ref:** `02-technical-spec.md > Model registry`

  **What to build:**
  - Model registry and Hugging Face downloader.
  - Exact official Qwen repository resolution.
  - Download official weak Q4_0, strong Q4_0, and strong Q8_0 candidates.
  - SHA-256 and license manifest.
  - Resume support and clear failure messages.
  - Never add model files to Git.

  **Acceptance:**
  - At minimum weak Q4_0, strong Q4_0, and strong Q8_0 are present.
  - Manifest contains repo, revision, filename, quant, hash, bytes, and Apache-2.0 license.
  - Hash verification can run independently.

  **Verify:**
  ```bash
  make models
  a64pilot models verify
  jq '.models | length' artifacts/model-manifest.json
  git status --ignored --short models/
  ```

- [ ] **5. Build the deterministic incident-triage workload**

  **Spec ref:** `01-scope-and-prd.md > FR-4 and FR-5`; `02-technical-spec.md > Incident-triage workload`

  **What to build:**
  - JSON schema, strict parser, safe tool allowlist, and mock tool fixtures.
  - At least 60 original synthetic incident cases in the required distribution.
  - Fixed 40/20 calibration/test split and leakage guard.
  - Prompt template shared by all candidates.
  - Objective scoring implementation.
  - Three handpicked demo requests.

  **Acceptance:**
  - Case schema validates.
  - Split hashes are stable.
  - Safety validator rejects unknown/destructive tools.
  - Scoring has deterministic unit tests.
  - Expected labels are never inserted into model prompts.

  **Verify:**
  ```bash
  a64pilot benchmark quality --validate-only
  pytest -q tests/test_agent_schema.py tests/test_quality.py
  jq -s 'length' demo/cases.jsonl
  ```

- [ ] **6. Implement process manager and OpenAI-compatible proxy**

  **Spec ref:** `02-technical-spec.md > Process manager`; `OpenAI-compatible proxy`

  **What to build:**
  - Typed command builder for pinned `llama-server` options.
  - Process lifecycle, port reservation, logs, readiness, RSS sampler, affinity.
  - Async streaming client with TTFT timing.
  - Proxy endpoints and baseline strong-only route.
  - CPU-only debug metadata.
  - Safe cleanup on exceptions/signals.

  **Acceptance:**
  - `make smoke` launches a CPU-only server and returns valid structured incident JSON.
  - Curl and Python demo client work.
  - No orphan server remains after exit.
  - Startup/backend logs are archived.

  **Verify:**
  ```bash
  make smoke
  python demo/demo-client.py --smoke
  curl -fsS http://127.0.0.1:8088/health | jq .
  pytest -q tests/test_commands.py tests/test_api.py
  ```

- [ ] **7. Implement raw benchmark instrumentation**

  **Spec ref:** `03-benchmark-protocol.md`; `02-technical-spec.md > Benchmark data schema`

  **What to build:**
  - Microbenchmark wrapper using pinned `llama-bench --help` discovery.
  - Service benchmark with warmups, repetitions, streaming timings, concurrency.
  - RSS sampling for process trees.
  - Optional `perf stat` collector with graceful permission fallback.
  - Versioned JSONL/CSV raw store and integrity hashes.
  - Statistical summary and bootstrap intervals.

  **Acceptance:**
  - A tiny generic and KleidiAI benchmark completes.
  - Raw rows include command, model hash, backend, timing, memory, and proof flags.
  - Summary regeneration is deterministic.
  - Failed runs remain visible.

  **Verify:**
  ```bash
  a64pilot benchmark micro --quick
  a64pilot benchmark service --quick
  a64pilot report --raw-only
  pytest -q tests/test_statistics.py tests/test_claim_integrity.py
  ```

- [ ] **8. Implement the staged, resumable auto-tuner**

  **Spec ref:** `02-technical-spec.md > Staged search algorithm`; `Pareto selection`

  **What to build:**
  - Host-derived thread and affinity candidates.
  - Bounded Stage A–E search with wall-clock budget.
  - Cache/resume keyed by binary/model/config/system hashes.
  - Candidate feasibility and Pareto frontier.
  - Knee-point selection and safe fallback.
  - `optimized-profile.yaml` generation.

  **Acceptance:**
  - Search never explodes into an unbounded Cartesian product.
  - Re-running reuses valid candidate results.
  - The selected profile references only measured candidates.
  - If no candidate passes, a documented safe profile is emitted.

  **Verify:**
  ```bash
  a64pilot optimize --max-minutes 15 --quick
  yq . artifacts/optimized-profile.yaml || cat artifacts/optimized-profile.yaml
  pytest -q tests/test_pareto.py tests/test_optimizer.py
  ```

- [ ] **9. Calibrate and validate the quality-gated cascade**

  **Spec ref:** `02-technical-spec.md > Complexity router and cascade`

  **What to build:**
  - Transparent request complexity feature extractor.
  - Threshold grid over calibration split.
  - Weak output schema/safety/internal-consistency validator.
  - Automatic escalation to strong model.
  - Held-out freeze guard.
  - Strong-only fallback if cascade quality fails.

  **Acceptance:**
  - Calibration uses only 40 calibration cases.
  - Selected threshold is frozen before test evaluation.
  - Held-out safety is 100% or cascade is rejected.
  - Quality gate calculation is visible in report data.
  - Route share and escalation rate are recorded.

  **Verify:**
  ```bash
  a64pilot benchmark quality --calibrate
  a64pilot benchmark quality --held-out --frozen
  jq . artifacts/quality-results.json
  pytest -q tests/test_router.py tests/test_quality_gate.py
  ```

- [ ] **10. Run final benchmark and render evidence dashboard**

  **Spec ref:** `03-benchmark-protocol.md > Baselines and ablations`; `02-technical-spec.md > Report design`

  **What to build/run:**
  - Execute A0–A4 final protocol on one named Arm64 Linux target.
  - Produce repeated raw rows, summaries, confidence intervals, and fair pairings.
  - Generate offline HTML/Markdown/JSON/CSV and required charts.
  - Generate claim objects and CPU-only/KleidiAI proof cards.
  - Redact public artifacts.

  **Acceptance:**
  - At least A0–A3 have complete evidence; A4 is included only if feasible.
  - Main Arm claim uses the same Q4_0 model/config except backend and rejects fallback warnings.
  - No CI crosses a misleadingly asserted claim.
  - Every headline value has source run IDs.
  - `report.html` opens with no network access.

  **Verify:**
  ```bash
  make benchmark
  make optimize
  make report
  make verify-claims
  python -m http.server 8000 -d artifacts
  ```

- [ ] **11. Add optional Arm Performix and RK3588 evidence without blocking**

  **Spec ref:** `02-technical-spec.md > perf and Performix`; `RK3588 optional extension`

  **What to build/run:**
  - If Arm MCP/Performix is configured, run the supplied profiling prompt for generic and KleidiAI representative binaries.
  - Save hotspot summaries and link them from the report.
  - If the user’s RK3588S target is reachable, run doctor/smoke and an affinity portability experiment.
  - Clearly separate optional target results from main cloud headline.

  **Acceptance:**
  - Core build remains green when neither optional target is available.
  - Optional evidence is labeled with independent system manifests.
  - No mixed-machine speedup is calculated.

  **Verify:**
  ```bash
  make performix || true
  make rk3588-smoke || true
  make verify
  ```

- [ ] **12. Finish public repository and Devpost handoff**

  **Spec ref:** `00-competition-brief.md`; `05-devpost-submission-draft.md`; `06-video-script.md`

  **What to build:**
  - Final English README with 30-second overview, architecture, benchmark table, quickstart, reproduction, limitations, licenses.
  - Render final Devpost text from claim objects.
  - Generate screenshot candidates and caption file.
  - Generate a timed three-minute video script with actual metrics.
  - Add testing instructions and free-access demo instructions.
  - Run secret/license/link/placeholder checks.
  - If `gh auth status` succeeds, create/push public repository or push current repo; otherwise leave exact commands in `artifacts/publish-commands.txt`.
  - Create final submission checklist with deadline.

  **Acceptance:**
  - No `{{...}}`, `TBD`, fake metric, broken link, secret, or private hostname remains.
  - Public repo contains all source and instructions but no model binaries.
  - README identifies Cloud AI and explains meaningful new work during challenge.
  - Video is under three minutes if produced.
  - `make verify` and `make submission` pass from a clean checkout plus model download.

  **Verify:**
  ```bash
  make verify
  make submission
  git status --short
  grep -RInE '\{\{|TBD|TODO_METRIC|YOUR_RESULT' README.md artifacts/devpost-writeup-final.md && exit 1 || true
  gh auth status || true
  ```

## Final agent report

At completion, write `FINAL_HANDOFF.md` containing:

- exact Arm target class and redacted system summary;
- selected profile;
- benchmark headline values and source claim IDs;
- quality/safety outcome;
- commands that pass;
- public repository URL or publish commands;
- Devpost text path;
- screenshot/video paths;
- optional work completed or skipped;
- any honest limitation a judge should know.
