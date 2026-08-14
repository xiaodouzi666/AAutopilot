SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

UV ?= uv
UV_RUN ?= $(UV) run --frozen --no-editable
PYTHON_VERSION ?= 3.11
JOBS ?= 4
BUILD_VARIANT ?= all

# Optional, intentionally explicit pass-through flags.
BOOTSTRAP_FLAGS ?=
DOCTOR_FLAGS ?=
BUILD_FLAGS ?=
MODEL_FLAGS ?= --minimum
SMOKE_FLAGS ?=
BENCHMARK_FLAGS ?= --quick --repetitions 1
PROBE_FLAGS ?= --repetitions 3 --max-minutes 20
TUNE_FLAGS ?= --quick --max-candidates 8 --calibration-cases 4 --finalists 2
OPTIMIZE_FLAGS ?=
A4_CALIBRATION_FLAGS ?=
A4_HELD_OUT_FLAGS ?=
REPORT_FLAGS ?=
SERVE_FLAGS ?=
DEMO_FLAGS ?=
VERIFY_FLAGS ?=
SUBMISSION_FLAGS ?=
PERFORMIX_ARGS ?=

.PHONY: help sync bootstrap doctor build models verify-models smoke fixture-smoke probes benchmark tune \
	select a4-calibrate a4-held-out a4 optimize report serve demo verify-source verify \
	verify-backends verify-probes verify-report-integrity verify-claims \
	submission performix clean-generated

help: ## Show stable project commands and overridable flags.
	@printf '%s\n' \
	  'AArch64 Autopilot commands' \
	  '' \
	  '  make doctor           Record hardware, topology, memory, and toolchain provenance' \
	  '  make bootstrap        Install/sync dependencies, build both backends, download models' \
	  '  make build            Build generic and KleidiAI llama.cpp from one pinned commit' \
	  '  make models           Download the reviewed minimum Qwen GGUF model set' \
	  '  make smoke            Run one real held-out case through both CPU-only backends' \
	  '  make fixture-smoke    Run the labelled non-evidence fixture path' \
	  '  make probes           Run bounded micro/startup/p1/p2 supporting probes' \
	  '  make benchmark        Run the bounded real Arm64 benchmark (quick by default)' \
	  '  make tune             Run the topology-derived A3 calibration/finalist search' \
	  '  make a4               Calibrate/freeze A4, then run its one-time held-out gate' \
	  '  make optimize         Benchmark, select a quality-feasible profile, and render report' \
	  '  make report           Regenerate the strict evidence report from raw rows' \
	  '  make serve            Start the local OpenAI-compatible API (set SERVE_FLAGS)' \
	  '  make demo             Start the API/report demo (set DEMO_FLAGS as needed)' \
	  '  make verify-source    Run source/data/fixture checks without performance claims' \
	  '  make verify           Run all source, backend, artifact, provenance, and claim gates' \
	  '  make submission       Render strict English Devpost materials from measured evidence' \
	  '  make performix        Run an explicitly supplied optional profiler command' \
	  '' \
	  'Common overrides:' \
	  '  JOBS=4 BUILD_VARIANT=all BUILD_FLAGS="..." MODEL_FLAGS="--minimum"' \
	  '  PROBE_FLAGS="--repetitions 3 --max-minutes 20" BENCHMARK_FLAGS="--quick --repetitions 1"' \
	  '  A4_CALIBRATION_FLAGS="..."' \
	  '  A4_HELD_OUT_FLAGS="..." REPORT_FLAGS="--allow-pending"' \
	  '  SERVE_FLAGS="--upstream http://127.0.0.1:18180" DEMO_FLAGS="--fixture"' \
	  '  VERIFY_FLAGS="--source-only" SUBMISSION_FLAGS="--allow-pending"'

sync: ## Rebuild and install the locked non-editable Python package.
	$(UV) sync --frozen --extra dev --no-editable --reinstall-package aarch64-autopilot

bootstrap: ## Prepare an Arm64 Linux target end to end.
	./scripts/bootstrap.sh --install-system-deps --jobs "$(JOBS)" $(BOOTSTRAP_FLAGS)

doctor: ## Inspect the host and write redacted public provenance.
	$(UV_RUN) a64pilot doctor $(DOCTOR_FLAGS)

build: ## Build fair generic/KleidiAI CPU-only binaries.
	./scripts/build-llama.sh "$(BUILD_VARIANT)" --jobs "$(JOBS)" $(BUILD_FLAGS)

models: ## Download and manifest reviewed official models.
	$(UV_RUN) python scripts/download-models.py $(MODEL_FLAGS)

verify-models: ## Rehash every downloaded model against its manifest.
	$(UV_RUN) a64pilot models verify

smoke: ## Run the fastest real dual-backend benchmark; fixture output is never evidence.
	$(UV_RUN) a64pilot benchmark fair --limit 1 --repetitions 1 $(SMOKE_FLAGS)

fixture-smoke: ## Exercise the API safely on non-Arm hosts without creating claims.
	$(UV_RUN) a64pilot smoke --fixture

probes: ## Run the bounded supporting micro/startup/token/p1/p2 evidence matrix.
	$(UV_RUN) a64pilot benchmark probes $(PROBE_FLAGS)

benchmark: ## Run real A0-A3 measurements; defaults are deadline-safe and bounded.
	$(UV_RUN) a64pilot benchmark all $(BENCHMARK_FLAGS)

tune: ## Run bounded topology-derived A3 calibration and held-out finalist validation.
	$(UV_RUN) a64pilot benchmark tune $(TUNE_FLAGS)

select: ## Select a measured quality-feasible Pareto profile without rerunning inference.
	$(UV_RUN) a64pilot optimize $(OPTIMIZE_FLAGS)

a4-calibrate: ## Run 40 real weak/strong cases and write the immutable A4 policy.
	$(UV_RUN) a64pilot benchmark quality --calibrate $(A4_CALIBRATION_FLAGS)

a4-held-out: ## Replay the frozen A4 policy once on all 20 held-out cases.
	$(UV_RUN) a64pilot benchmark quality --held-out --frozen $(A4_HELD_OUT_FLAGS)

a4: a4-calibrate a4-held-out ## Complete A4 quality admission; gate rejection still ships A3.

optimize: probes benchmark select a4 report ## Run probes plus A0-A4 and render the report.

report: ## Render evidence and fail unless a fair measured claim exists.
	$(UV_RUN) a64pilot report $(REPORT_FLAGS)

serve: ## Launch the selected/external upstream through the localhost proxy.
	$(UV_RUN) a64pilot serve $(SERVE_FLAGS)

demo: ## Launch the local API/report experience.
	$(UV_RUN) a64pilot demo $(DEMO_FLAGS)

verify-source: ## Run deterministic checks that are valid on any supported developer host.
	$(UV_RUN) ruff check src tests scripts
	$(UV_RUN) pytest -q
	$(UV_RUN) a64pilot benchmark quality --validate-only
	$(UV_RUN) a64pilot smoke --fixture
	$(UV_RUN) a64pilot verify --source-only
	$(UV_RUN) python scripts/redact-artifacts.py --check artifacts

verify-backends: ## Verify build caches plus measured CPU-only generic/KleidiAI rows.
	./scripts/verify-cpu-only.sh

verify-probes: ## Replay probe raw hashes, semantics, manifests, and current binary/model hashes.
	$(UV_RUN) a64pilot verify-probes

verify-report-integrity: ## Verify report hashes and embedded target provenance.
	$(UV_RUN) a64pilot verify-report-integrity

verify-claims: ## Resolve every public claim back to measured raw rows.
	$(UV_RUN) a64pilot verify-claims

verify: verify-source verify-backends verify-probes verify-report-integrity verify-claims ## Run the complete submission gate.
	$(UV_RUN) a64pilot verify $(VERIFY_FLAGS)

submission: ## Render final submission copy; pending data fails by default.
	$(UV_RUN) a64pilot submission $(SUBMISSION_FLAGS)

performix: ## Optional supporting profile; unavailable status is explicit and nonzero.
	./scripts/run-performix.sh $(PERFORMIX_ARGS)

clean-generated: ## Print generated locations; removal is intentionally manual.
	@printf '%s\n' \
	  'No files were removed.' \
	  'Generated data lives under artifacts/, build/, models/, and third_party/llama.cpp/.'
