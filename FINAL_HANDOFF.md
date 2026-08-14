# AArch64 Autopilot — Final Handoff

## Status and scope

This document records the **latest verified evidence** for the published Arm Create: AI
Optimization Challenge entry as of 2026-08-14. It is a handoff index, not a new source of
measurement truth. The authoritative order is:

1. the attested evidence archive and its GitHub attestation;
2. the public Arm64 workflow run and exact measured source commit;
3. the machine-readable files under [`artifacts/`](artifacts/);
4. the rendered report, repository documentation, video, and Devpost page;
5. this handoff.

Do not combine identifiers from different evidence runs. If later evidence supersedes this
snapshot, update the complete evidence-identity block, all dependent digests, claims, media, and
public URLs as one unit.

## Evidence identity

| Field | Latest verified value |
|---|---|
| Repository | [`xiaodouzi666/AAutopilot`](https://github.com/xiaodouzi666/AAutopilot) |
| Public default branch | `main` |
| Public publication commit | [`f29a81db52a062d547b0c6d9c73487da3c986d4b`](https://github.com/xiaodouzi666/AAutopilot/commit/f29a81db52a062d547b0c6d9c73487da3c986d4b) |
| Measured source commit | [`6d8e21818fc0ef0202ec85236bcec6d20e908f23`](https://github.com/xiaodouzi666/AAutopilot/commit/6d8e21818fc0ef0202ec85236bcec6d20e908f23) |
| Workflow run | [`31778419786`](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786), attempt `1` |
| Arm64 job | [`94698993956`](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786/job/94698993956), `success` |
| Evidence tag | `arm64-evidence-run-31778419786` |
| Evidence release | [Latest published release](https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31778419786) |
| Attestation | [GitHub attestation `40687167`](https://github.com/xiaodouzi666/AAutopilot/attestations/40687167) |
| Attested subject | `aarch64-autopilot-evidence.tar.gz` |
| Subject SHA-256 | `b3f2a719a91e483fb984bc83d3de7435d4689d158129fef2e829e1168659f829` |
| Rekor log index | `2463537656` |
| Devpost | [Published project](https://devpost.com/software/aarch64-autopilot-self-optimizing-agentic-ai-on-arm-cpus) |
| Devpost receipt | `submitted_at=2026-08-14T02:04:58.469-04:00` |
| Public video | [YouTube `2wZx67_iaSw`](https://youtu.be/2wZx67_iaSw) |

The publication commit is the direct child of the measured source commit and contains the final
public documentation and curated artifacts. The annotated evidence tag resolves to the measured
source commit, not to the later publication-only commit.

## Exact target and build

The performance evidence came from the official GitHub-hosted `ubuntu-24.04-arm` runner, not the
macOS development host.

| Target field | Recorded value |
|---|---|
| Architecture / OS | `aarch64` / Linux |
| Kernel | `6.17.0-1022-azure` |
| CPU topology | 4 physical cores, 4 allowed logical CPUs (`0-3`), one NUMA node |
| Memory | `16,722,046,976` bytes, approximately 15.57 GiB |
| Recorded Arm features | dot product, FP16, BF16, I8MM, SVE, and SVE2 |
| Not recorded as supported | SME and SME2 |
| Compiler | `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0` |
| `llama.cpp` source | `a94d563ed801d1da1b8c2432946de07d0231bb3d` |
| Generic build | Release, `GGML_CPU_KLEIDIAI=OFF`, CPU-only configured |
| Optimized build | Release, `GGML_CPU_KLEIDIAI=ON`, CPU-only configured, runtime marker verified |

The full public target and build records are
[`artifacts/system-info.json`](artifacts/system-info.json) and
[`artifacts/build-manifest.json`](artifacts/build-manifest.json). GPU backends were disabled in
both builds, and accepted measured commands used the CPU-only interface.

### Pinned model inventory

| Role | Model | Revision | File SHA-256 | Reviewed inventory |
|---|---|---|---|---|
| Strong, fair A1/A2 and shipping | Qwen2.5 1.5B Instruct Q4_0 | `91cad51170dc346986eccefdc2dd33a9da36ead9` | `dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb` | 197 Q4_0 tensors plus the single disclosed Q6_K `output.weight` |
| Weak, A4 experiment only | Qwen2.5 0.5B Instruct Q4_0 | `9217f5db79a29953eb74d5343926648285ec7e67` | `7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed` | 169 Q4_0 tensors; no reviewed fallback |
| Strong Q8 reference | Qwen2.5 1.5B Instruct Q8_0 | `91cad51170dc346986eccefdc2dd33a9da36ead9` | `d7efb072e7724d25048a4fda0a3e10b04bdef5d06b1403a1c93bd9f1240a63c8` | 198 Q8_0 tensors |

Exact byte sizes, repository names, tensor-inventory hashes, and licenses are in
[`artifacts/model-manifest.json`](artifacts/model-manifest.json). Model weights are not
redistributed by this repository.

## Shipping profile

The shipped deployment is **A3 strong-only**. A4 did not replace it.

| Profile field | Value |
|---|---|
| Profile ID | `kleidiai-q4-0-t4-b128-u64-p1-c2048` |
| Evidence state | `measured` |
| Backend / model role | `kleidiai` / `strong` |
| Quantization | `Q4_0` |
| Threads | `4` |
| Batch / micro-batch | `128` / `64` |
| Parallel slots | `1` |
| Context | `2048` |
| Affinity override | none; all allowed CPUs remain available |
| Quality / safety | `73.875` / `100.0` |
| Schema failures | `0` |
| p95 end-to-end latency | `7201.44694385 ms` |
| Per-request throughput | `0.16428987005272092 requests/s` |
| Peak RSS | `1938.04296875 MiB` |
| Selection basis | `frozen_calibration_finalist` |
| Held-out cases | `20` |

The complete profile, all 20 source run IDs, gate receipt, and search-plan hashes are in
[`artifacts/optimized-profile.yaml`](artifacts/optimized-profile.yaml).

## Claim registry

The fair A1/A2 comparison used the same official strong Q4_0 model and 20 matched split-v2 cases,
giving 40 formal source rows. Mean time to first token was the sole prospectively registered
primary publication outcome.

| Claim ID | Claim | Exact value | Paired 95% bootstrap interval | Demonstrated | Publication treatment |
|---|---|---:|---:|---|---|
| `fair_q4_0_mean_ttft_reduction` | Mean TTFT reduction | `1.4983603642459042%` | `[0.5136389656173509%, 2.60026064704425%]` | Yes | Primary gate passed; rounded public value `1.498%` |
| `fair_q4_0_p95_latency_reduction` | p95 end-to-end latency reduction | `4.3098000986850495%` | `[-15.486348258135404%, 48.745572560011766%]` | No | Transparent secondary; not promoted as an improvement |
| `fair_q4_0_request_throughput_increase` | Median per-request throughput increase | `2.022786288942867%` | `[-3.3630233641974843%, 10.793234672532613%]` | No | Transparent secondary; not promoted as an improvement |

A1 quality was `72.975`; A2 quality was `73.875`. Both had minimum safety `100.0` and zero
schema failures. Exact formulas and every source-row identifier are in
[`artifacts/claims.json`](artifacts/claims.json).

## Gates and receipts

| Gate | Latest verified result | Authoritative evidence |
|---|---|---|
| Public repository and license | Pass; repository public, Apache-2.0 recognized | [Repository](https://github.com/xiaodouzi666/AAutopilot), [`LICENSE`](LICENSE) |
| Exact Arm target | Pass; public GitHub-hosted Arm64 job | [Run `31778419786`](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786), [`system-info.json`](artifacts/system-info.json) |
| Same-source build parity | Pass; both variants use `a94d563…` | [`build-manifest.json`](artifacts/build-manifest.json) |
| CPU-only execution | Pass; GPU backends/devices rejected by verifier | [`report.md`](artifacts/report.md) |
| KleidiAI proof | Pass; build, load, and validated-request marker chain | [`build-manifest.json`](artifacts/build-manifest.json) |
| Model identity | Pass; exact revisions, bytes, hashes, and tensor inventories | [`model-manifest.json`](artifacts/model-manifest.json) |
| Dataset and split | Pass; 60 cases, frozen split-v2 40 calibration / 20 held-out | [`quality-summary.json`](artifacts/quality-summary.json), [`demo/split-freeze-v2.json`](demo/split-freeze-v2.json) |
| A1/A2 quality and safety | Pass; both safety 100, zero schema failures | [`quality-summary.json`](artifacts/quality-summary.json) |
| Primary publication gate | Pass; positive lower bound `0.5136389656%` | [`claims.json`](artifacts/claims.json) |
| Secondary claims | Recorded but not demonstrated | [`claims.json`](artifacts/claims.json) |
| Selected A3 profile | Pass; held-out gate passed | [`optimized-profile.yaml`](artifacts/optimized-profile.yaml) |
| A4 admission | Rejected correctly; fail-closed to strong-only | [`cascade-status.json`](artifacts/cascade-status.json) |
| Source and evidence verification | Pass; 270 source tests plus backend, claim, provenance, and replay gates | [Successful job](https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786/job/94698993956) |
| Public redaction and secret scan | Pass | [`submission-checklist.md`](artifacts/submission-checklist.md) |
| Target demo receipt | Pass; Arm-run-bound validated response | [`arm-target-demo-receipt.json`](artifacts/submission/arm-target-demo-receipt.json) |
| Evidence package and provenance | Pass; release digest equals attested subject | [`evidence-index.json`](artifacts/evidence-index.json), [attestation](https://github.com/xiaodouzi666/AAutopilot/attestations/40687167) |
| Devpost receipt | Pass; published and submitted readback verified | [Devpost project](https://devpost.com/software/aarch64-autopilot-self-optimizing-agentic-ai-on-arm-cpus) |

## A4 disposition

A4 is preserved as a measured rejection, not advertised as a deployed cascade:

- 40 real calibration cases measured both Qwen2.5 0.5B and 1.5B components;
- freeze ID: `a0aee38577d836ff45b1079611ec66b3abc828ff4da4a57d17ce4ae144a05190`;
- policy ID: `34df5c9a566fbc373f98920b448858489ac0064799a72d2a3a6f36457c629bb6`;
- selected threshold: `null`;
- fallback policy: strong-only;
- frozen replay: 20 strong, 0 weak, 0 weak-then-strong, 0% escalation;
- `a4_admitted_by_quality_gate=false` and `performance_claim_eligible=false`;
- shipping profile: `a3-strong-only`.

This replay reused split-v2 cases already used by A0-A3. It is post-hoc quality/routing evidence,
not a new unseen confirmatory set and not a live-cascade latency, throughput, combined-RSS, or
deployment result. See [`artifacts/a4-frozen-policy.json`](artifacts/a4-frozen-policy.json),
[`artifacts/quality-results.json`](artifacts/quality-results.json), and
[`artifacts/cascade-status.json`](artifacts/cascade-status.json).

## Public surfaces

| Surface | URL |
|---|---|
| Repository | https://github.com/xiaodouzi666/AAutopilot |
| Apache-2.0 license | https://github.com/xiaodouzi666/AAutopilot/blob/main/LICENSE |
| Measured source commit | https://github.com/xiaodouzi666/AAutopilot/commit/6d8e21818fc0ef0202ec85236bcec6d20e908f23 |
| Publication commit | https://github.com/xiaodouzi666/AAutopilot/commit/f29a81db52a062d547b0c6d9c73487da3c986d4b |
| Successful Arm64 run | https://github.com/xiaodouzi666/AAutopilot/actions/runs/31778419786 |
| Evidence release | https://github.com/xiaodouzi666/AAutopilot/releases/tag/arm64-evidence-run-31778419786 |
| Build-provenance attestation | https://github.com/xiaodouzi666/AAutopilot/attestations/40687167 |
| Machine-readable claim index | https://github.com/xiaodouzi666/AAutopilot/blob/main/artifacts/claims.json |
| Offline report source | https://github.com/xiaodouzi666/AAutopilot/blob/main/artifacts/report.html |
| Natural-voice demo | https://youtu.be/2wZx67_iaSw |
| Published Devpost project | https://devpost.com/software/aarch64-autopilot-self-optimizing-agentic-ai-on-arm-cpus |

## Media registry

### Attested CI source video

- release asset: `a64pilot-demo-final.mp4`;
- duration: `139.215` seconds;
- SHA-256: `9e7322eead44d50de122776356137bb0c35d635aae1b134a296aea939bee7300`;
- manifest SHA-256: `ea60316dd02273c0ab89d779fb864530594f45d7dc973b8a441694ca9bf2d2fa`;
- mode: `final_measured`, no music, publishable;
- provenance: included inside the attested evidence subject.

The tracked manifest is
[`artifacts/submission/a64pilot-demo-final.manifest.json`](artifacts/submission/a64pilot-demo-final.manifest.json).

### Public natural-voice derivative

- YouTube: https://youtu.be/2wZx67_iaSw;
- release asset: `a64pilot-demo-natural-voice-run-31778419786.mp4`;
- video SHA-256: `e9f6a6d1f043fb943f21468f704e0fa711f850d47e32f743f703c1a1205dbeb3`;
- manifest SHA-256: `0990f01e992ceacc54b4352d4d15811bdf351e816da95169917f9f318545d565`;
- H.264 visual-stream SHA-256: `65408bbb2f004caeb7efab80d629d66f52eb974341df9d0a34ee51934895057e`;
- role: audio-only presentation derivative with unchanged visual stream;
- attestation status: the derivative is not itself CI-attested.

The original CI source remains the provenance-bearing media. The derivative's role and release
URLs are recorded in [`artifacts/evidence-index.json`](artifacts/evidence-index.json).

### Thumbnail and screenshots

- Devpost thumbnail source SHA-256:
  `17574e9cc240c3e9100e657ab50df7e181c9418c39ecd2c3b07f5e27318212e5`;
- public Devpost preview:
  https://d112y698adiu2z.cloudfront.net/photos/production/software_thumbnail_photos/005/097/643/datas/medium.png;
- four final screenshots and their frame/source hashes:
  [`artifacts/screenshots/manifest.json`](artifacts/screenshots/manifest.json).

## Release asset digests

These are GitHub's published SHA-256 values for the ten manually uploaded release assets. The
GitHub release UI also lists two automatically generated source archives.

| Asset | SHA-256 |
|---|---|
| `a4-frozen-policy.json` | `4ca15ee6a781e2c63b2fad285a0e5a947bc974742edd43c1dfacf4dc882b31c5` |
| `a64pilot-demo-final.manifest.json` | `ea60316dd02273c0ab89d779fb864530594f45d7dc973b8a441694ca9bf2d2fa` |
| `a64pilot-demo-final.mp4` | `9e7322eead44d50de122776356137bb0c35d635aae1b134a296aea939bee7300` |
| `a64pilot-demo-natural-voice-run-31778419786.manifest.json` | `0990f01e992ceacc54b4352d4d15811bdf351e816da95169917f9f318545d565` |
| `a64pilot-demo-natural-voice-run-31778419786.mp4` | `e9f6a6d1f043fb943f21468f704e0fa711f850d47e32f743f703c1a1205dbeb3` |
| `aarch64-autopilot-evidence.tar.gz` | `b3f2a719a91e483fb984bc83d3de7435d4689d158129fef2e829e1168659f829` |
| `arm-target-demo-receipt.json` | `d4feb35014be3b2c48d67783a63eb758c318e63dd5af8663e6b50a00ecc60ba3` |
| `cascade-status.json` | `b54367eb4980c897f46db9ef0b4a3a9e456daf0794ff40170a11e6561ba2d4c5` |
| `evidence-bundle.sha256` | `cbbea37a8320853aee34ad059670eaea1dca982ec522aa3e035a6c3a11f74a1a` |
| `quality-results.json` | `164fb9039b3b1ba967f0730be1f542fccf5b2640550cd2c78031a2310a715904` |

## Reproduction and verification commands

### Inspect the exact measured source

```bash
git clone https://github.com/xiaodouzi666/AAutopilot.git
cd AAutopilot
git fetch --tags origin
git checkout --detach arm64-evidence-run-31778419786
git rev-parse HEAD
```

The final command must print `6d8e21818fc0ef0202ec85236bcec6d20e908f23`.

### Verify the public evidence subject

```bash
gh release download arm64-evidence-run-31778419786 \
  -R xiaodouzi666/AAutopilot \
  -p aarch64-autopilot-evidence.tar.gz \
  -p evidence-bundle.sha256

sha256sum -c evidence-bundle.sha256
gh attestation verify aarch64-autopilot-evidence.tar.gz \
  -R xiaodouzi666/AAutopilot
tar -tzf aarch64-autopilot-evidence.tar.gz
```

### Run source-only checks without model weights

```bash
uv sync --frozen --extra dev --no-editable
make verify-source
uv run --frozen --no-editable ruff format --check src tests scripts
uv run --frozen --no-editable python scripts/check-final-placeholders.py
uv run --frozen --no-editable python scripts/generate-submission-assets.py --verify-only
```

Fixture mode is labelled non-evidence and cannot create or replace public performance claims.

### Reproduce the full Arm workflow

Use a Linux `aarch64` host with at least 4 cores, 8 GiB RAM, roughly 10-15 GiB free disk, normal
network access, Git, a C/C++ compiler, CMake, Ninja, and Python 3.11 or 3.12:

```bash
make doctor
make bootstrap
make smoke
make optimize
make verify
make submission
```

`make optimize` performs probes, A0-A3 measurement, profile selection, A4 calibration and frozen
replay, and strict report rendering. A fresh measurement is a new evidence run; it must not be
presented under the identifiers in this handoff unless all resulting hashes and receipts match.

### Exercise the selected API and report

```bash
make demo
curl -fsS http://127.0.0.1:8088/health
curl -fsS http://127.0.0.1:8088/v1/models
curl -fsS http://127.0.0.1:8088/metrics
python demo/demo-client.py --smoke --debug
```

The service binds to localhost by default.

## Optional work deliberately excluded from the core claim

The delivered state corresponds to the risk register's **Level 2 — Core technical submission**:
A0-A3 and the fair KleidiAI claim are complete, A4 was run and rejected honestly, and the report,
API demo, public repository, screenshots, video, release, attestation, and Devpost receipt exist.

- Arm Performix profiling was not required and is not a source of any speed claim.
- RK3588 portability profiling was not required and is not mixed with the cloud target.
- Energy and cloud-cost claims were omitted because the target supplied no credible energy
  counter or stable instance price.
- A4 was not skipped; its real calibration and replay are retained as rejected evidence, but no
  live cascade was shipped.
- The optional video was completed; its natural-voice version is explicitly separated from the
  attested CI source.

Absence of Performix, RK3588, energy, or cost evidence is not a missing core gate and must not be
filled with inferred or cross-machine numbers.

## Limitations and claim boundaries

- Results are specific to the recorded target, pinned runtime, exact model files, serving
  configuration, and synthetic incident-triage workload.
- The 60-case suite is an objective regression workload, not a general model-capability benchmark.
- Only the primary mean-TTFT interval demonstrated a positive improvement. The p95 and throughput
  intervals cross zero.
- A4 is post-hoc quality/routing replay evidence and supplies no live-cascade latency, throughput,
  combined-RSS, energy, or deployment claim.
- The KleidiAI proof covers configuration, model load, runtime marker, and validated request paths;
  it is not instruction-level or per-tensor microkernel tracing.
- The macOS/Apple Silicon development host is valid for source, fixture, package, and UI checks,
  but not for the Cloud AI performance claim.
- The public natural-voice video is an audio-only derivative and is not itself covered by the
  GitHub attestation.
- Public artifacts are redacted. Strict replay of the report from raw inputs requires the full
  attested archive and a compatible Arm capture environment.
- Runtime services bind to `127.0.0.1` by default; this is not a hosted production service.
- Devpost proves a published submission and receipt. Arm Developer Program membership, age,
  geography, conflict status, and other personal legal assertions are account-holder facts, not
  facts proved by this repository.
- Devpost does not expose the submitted custom-answer values through its post-submission read API.
  The live `submitted` relationship proves platform acceptance of required fields, not a public
  readback of each answer.

## Document and index semantics

- [`docs/10-final-submission-checklist.md`](docs/10-final-submission-checklist.md) is the reusable
  preflight template. The completed evidence-backed receipt is
  [`artifacts/submission-checklist.md`](artifacts/submission-checklist.md). Unchecked boxes in the
  reusable template do not override the completed receipt.
- In [`artifacts/evidence-index.json`](artifacts/evidence-index.json),
  `video.attested_source.attestation_status_at_index_creation="pending_post_workflow"` is a
  historical statement about index-generation time. It remains true even though the current
  top-level `index_state` is `final_publication_verified`, the top-level attestation status is
  `verified`, and the Devpost final readback is verified.
- Do not rewrite that historical field to imply that the attestation already existed when the
  index was generated. If a later schema needs another single publication summary, add a sibling
  `final_publication_status` field while preserving the historical value.

## Final handoff state

At this snapshot, the public repository, exact Arm run, measured claims, quality gates, release,
attestation, videos, thumbnail, Devpost page, and submission receipt are all present. The honest
shipping decision is A3 strong-only; A4 remains a recorded rejected experiment.
