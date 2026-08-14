# AArch64 Autopilot Evidence Report

**Evidence status:** measured (90 raw request rows).

**Frozen workload:** cases `8c73129b9b79…`; split schema v2.0 `92e312535402…`.

## Preregistered outcomes

- **Primary — Q4_0 mean time-to-first-token reduction:** 1.498%; paired 95% bootstrap interval 0.514 to 2.600%. The interval excludes zero on the positive side. This preregistered primary result passes the final improvement gate.  - Baseline: `a1-generic-q4-0`
  - Optimized: `a2-kleidiai-q4-0`
  - Formula: `(generic_q4_0_mean_ttft_ms - kleidiai_q4_0_mean_ttft_ms) / generic_q4_0_mean_ttft_ms * 100`
  - Source run IDs: 981858e4a4984d6196b55b59e47a3e7a, 901a7bb37188425f97b490f55f5543d9, 47ce01dae8c64fdd80e33796b7b407ba, 5be9cbe14a52488f92789d3fb1033720, 4b917c38161743e0bb7360f5ca4f9f5b, b0b219d58f9f457e8b615fd615124e54, c12c86389327458d939eec789318a645, 544a7d74a2254f78b66d580226be5690, 872356a907d24382a3b55f8a0804dd66, 226a693ee11749f294e9d0dd5b01573c, 8fa8d87b34ed47f4a91710a2887983fb, d3aece0d510541a480c6e8cda6c4395a, fee81834b1cd4926afa36fc487fbcc05, 9db9a0da07c140b788d54742fb5c313c, c068ce286d7249379f901a5871ea3aaa, 77076edb245c45f784a14b2b93f8ada8, b37d6d53801c44298c65169c0c5b39b2, 0a7165c4b4fd48f7956f5b05086f7590, 765c5f8552724bbc9e8822480ac136aa, d0abf06f62a74e66b2074e5c9fe7020c, d6a6106a55fd4e9da6d82e5a7e30f3ce, e57042865e2e4955a7551560ecee7bad, 380bd09431314d1b8cccc0d4828802cc, 7a23de080be34f8a835b04765ea2126c, a592923cce714d02a3ccfa53bcf718b7, 33014545400b4e6dbd64af3f2f38c1c7, 1fdff8216ffb4854a6cbbe5816467738, b5de1352be154c24925da8170f3c9dba, e875acaa58024c05b81b4d006bd4aec7, d977169696934a8f83c69a3a1d2c6ad7, 5930cfc8ae8948b3b206a644b024a534, cc31a94cc52541d599dafb90b3e34ef4, ea1d839bdd614d4db7ee0543fe1f771a, 3eaf143aa70948dab7b02be01467d28c, 7bdc987577144344ad294e7c8d1f3dac, 999f1a78d47e4b85acf02f61b2737fbf, 0d8f696c72fd4eeca516026bd787d523, a61ab94b3f6d4fc4a20965fc9e199ce1, 389ad52a6fb14bdca88b0432dc6dc496, a663803c85654a26b26c28349a5ee7b7
- **Transparent secondary — Q4_0 p95 end-to-end latency reduction:** 4.310%; paired 95% bootstrap interval -15.486 to 48.746%. The interval crosses zero. This secondary outcome is always disclosed and cannot unlock final publication.  - Baseline: `a1-generic-q4-0`
  - Optimized: `a2-kleidiai-q4-0`
  - Formula: `(generic_q4_0_p95_ms - kleidiai_q4_0_p95_ms) / generic_q4_0_p95_ms * 100`
  - Source run IDs: 981858e4a4984d6196b55b59e47a3e7a, 901a7bb37188425f97b490f55f5543d9, 47ce01dae8c64fdd80e33796b7b407ba, 5be9cbe14a52488f92789d3fb1033720, 4b917c38161743e0bb7360f5ca4f9f5b, b0b219d58f9f457e8b615fd615124e54, c12c86389327458d939eec789318a645, 544a7d74a2254f78b66d580226be5690, 872356a907d24382a3b55f8a0804dd66, 226a693ee11749f294e9d0dd5b01573c, 8fa8d87b34ed47f4a91710a2887983fb, d3aece0d510541a480c6e8cda6c4395a, fee81834b1cd4926afa36fc487fbcc05, 9db9a0da07c140b788d54742fb5c313c, c068ce286d7249379f901a5871ea3aaa, 77076edb245c45f784a14b2b93f8ada8, b37d6d53801c44298c65169c0c5b39b2, 0a7165c4b4fd48f7956f5b05086f7590, 765c5f8552724bbc9e8822480ac136aa, d0abf06f62a74e66b2074e5c9fe7020c, d6a6106a55fd4e9da6d82e5a7e30f3ce, e57042865e2e4955a7551560ecee7bad, 380bd09431314d1b8cccc0d4828802cc, 7a23de080be34f8a835b04765ea2126c, a592923cce714d02a3ccfa53bcf718b7, 33014545400b4e6dbd64af3f2f38c1c7, 1fdff8216ffb4854a6cbbe5816467738, b5de1352be154c24925da8170f3c9dba, e875acaa58024c05b81b4d006bd4aec7, d977169696934a8f83c69a3a1d2c6ad7, 5930cfc8ae8948b3b206a644b024a534, cc31a94cc52541d599dafb90b3e34ef4, ea1d839bdd614d4db7ee0543fe1f771a, 3eaf143aa70948dab7b02be01467d28c, 7bdc987577144344ad294e7c8d1f3dac, 999f1a78d47e4b85acf02f61b2737fbf, 0d8f696c72fd4eeca516026bd787d523, a61ab94b3f6d4fc4a20965fc9e199ce1, 389ad52a6fb14bdca88b0432dc6dc496, a663803c85654a26b26c28349a5ee7b7
- **Transparent secondary — Q4_0 median per-request throughput increase:** 2.023%; paired 95% bootstrap interval -3.363 to 10.793%. The interval crosses zero. This secondary outcome is always disclosed and cannot unlock final publication.  - Baseline: `a1-generic-q4-0`
  - Optimized: `a2-kleidiai-q4-0`
  - Formula: `(kleidiai_q4_0_median_rps - generic_q4_0_median_rps) / generic_q4_0_median_rps * 100`
  - Source run IDs: 981858e4a4984d6196b55b59e47a3e7a, 901a7bb37188425f97b490f55f5543d9, 47ce01dae8c64fdd80e33796b7b407ba, 5be9cbe14a52488f92789d3fb1033720, 4b917c38161743e0bb7360f5ca4f9f5b, b0b219d58f9f457e8b615fd615124e54, c12c86389327458d939eec789318a645, 544a7d74a2254f78b66d580226be5690, 872356a907d24382a3b55f8a0804dd66, 226a693ee11749f294e9d0dd5b01573c, 8fa8d87b34ed47f4a91710a2887983fb, d3aece0d510541a480c6e8cda6c4395a, fee81834b1cd4926afa36fc487fbcc05, 9db9a0da07c140b788d54742fb5c313c, c068ce286d7249379f901a5871ea3aaa, 77076edb245c45f784a14b2b93f8ada8, b37d6d53801c44298c65169c0c5b39b2, 0a7165c4b4fd48f7956f5b05086f7590, 765c5f8552724bbc9e8822480ac136aa, d0abf06f62a74e66b2074e5c9fe7020c, d6a6106a55fd4e9da6d82e5a7e30f3ce, e57042865e2e4955a7551560ecee7bad, 380bd09431314d1b8cccc0d4828802cc, 7a23de080be34f8a835b04765ea2126c, a592923cce714d02a3ccfa53bcf718b7, 33014545400b4e6dbd64af3f2f38c1c7, 1fdff8216ffb4854a6cbbe5816467738, b5de1352be154c24925da8170f3c9dba, e875acaa58024c05b81b4d006bd4aec7, d977169696934a8f83c69a3a1d2c6ad7, 5930cfc8ae8948b3b206a644b024a534, cc31a94cc52541d599dafb90b3e34ef4, ea1d839bdd614d4db7ee0543fe1f771a, 3eaf143aa70948dab7b02be01467d28c, 7bdc987577144344ad294e7c8d1f3dac, 999f1a78d47e4b85acf02f61b2737fbf, 0d8f696c72fd4eeca516026bd787d523, a61ab94b3f6d4fc4a20965fc9e199ce1, 389ad52a6fb14bdca88b0432dc6dc496, a663803c85654a26b26c28349a5ee7b7

## Candidate summary

| Candidate | Stage | Backend | n | p50 ms | p95 ms | Quality | Safety | Peak RSS MB |
|---|---|---|---:|---:|---:|---:|---:|---:|
| a0-generic-q8 | reference | generic | 10 | 7670.63 | 10762.07 | 67.25 | 80.00 | 3357.3 |
| a1-generic-q4-0 | baseline | generic | 20 | 6191.91 | 7526.39 | 72.97 | 100.00 | 1940.0 |
| a2-kleidiai-q4-0 | kleidiai | kleidiai | 20 | 6069.28 | 7202.02 | 73.88 | 100.00 | 1940.5 |
| kleidiai-q4-0-t4-b128-u128-p1-c2048 | tuned | kleidiai | 20 | 6109.90 | 7142.41 | 73.88 | 100.00 | 1940.5 |
| kleidiai-q4-0-t4-b128-u64-p1-c2048 | tuned | kleidiai | 20 | 6086.81 | 7201.45 | 73.88 | 100.00 | 1938.0 |

## A4 quality-gated cascade

**Status:** `calibration-fallback-strong-only`. **Shipping profile:** `a3-strong-only`.

no calibration threshold passed the frozen quality gate

Held-out route distribution: weak 0.00%, strong 100.00%, weak→strong 0.00%; escalation rate 0.00% of weak attempts. The held-out quality gate passed.

This A4 result is measured quality/routing evidence only. Its component replay is explicitly ineligible for live-cascade latency, throughput, or combined-memory claims.

frozen-policy quality replay on split-v2; split-v2 was already used by the published A0-A3 run and is not claimed as a newly unseen confirmatory set for A4

## Evidence contract

The fair Arm-specific comparison holds the target, source commit, official Q4_0 model checksum, prompt set, sampling, affinity, threads, batch, micro-batch, concurrency, and lifecycle constant. The intended variable is only the generic versus KleidiAI CPU backend. The exact pinned strong-model inventory contains 197 Q4_0 tensors and one disclosed Q6_K `output.weight`; that single fallback is allowed only when SHA-256, size, and the full GGUF header inventory match. CPU-only proof and the primary KleidiAI Q4 marker are required, and any additional or different fallback is rejected.

Split v1's test cases were executed during failed run6 and then used for error analysis, so they
are retired from final evaluation. Before the next run, split v2 selected 20 final-holdout cases
from the 36 never-executed candidates by a frozen stratified hash procedure whose only inputs were
`category` and `case_id`. Only split v2 is described as the unseen final holdout; its complete
audit manifest is `demo/split-freeze-v2.json`.

Mean TTFT reduction is the prospectively registered primary outcome. P95 end-to-end latency
reduction and median per-request throughput increase are transparent secondary outcomes. All
three are rendered from the same 20 paired cases (40 formal A1/A2 source rows); only a positive
primary interval whose lower bound exceeds zero can unlock final publication.

The submitted API profile remains strong-only until a separately measured live-cascade performance
run and deployment profile exist. Its public boundary applies constrained triage JSON and fails
closed when schema, read-only tool policy, safety, or consistency validation fails. A4 quality
admission never substitutes component replay timings for live multi-runtime evidence.

## Limitations

- Results apply only to the recorded target, model files, runtime commit, and workload.
- The synthetic incident suite is not a general LLM capability benchmark.
- No energy or cloud-cost claim is made without a credible counter or supplied price.
- Fixture responses are excluded from every performance claim.
- A4 calibration replays measured weak/strong outputs for quality and routing; it is not live-cascade latency, throughput, or combined-RSS evidence.

Generated at 2026-08-14T07:48:53.476803+00:00. The repository carries the sanitized formal rows in
`benchmark-results.json` plus `claims.json`; the attested release bundle carries the full redacted
`raw/` capture and integrity receipts.