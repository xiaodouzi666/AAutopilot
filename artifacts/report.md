# AArch64 Autopilot Evidence Report

**Evidence status:** measured (90 raw request rows).

**Frozen workload:** cases `8c73129b9b79…`; split schema v2.0 `92e312535402…`.

## Preregistered outcomes

- **Primary — Q4_0 mean time-to-first-token reduction:** 1.794%; paired 95% bootstrap interval 0.652 to 2.955%. The interval excludes zero on the positive side. This preregistered primary result passes the final improvement gate.  - Baseline: `a1-generic-q4-0`
  - Optimized: `a2-kleidiai-q4-0`
  - Formula: `(generic_q4_0_mean_ttft_ms - kleidiai_q4_0_mean_ttft_ms) / generic_q4_0_mean_ttft_ms * 100`
  - Source run IDs: b169922e39024a9bafda9693ecbff34d, 271ae569b28a448182781aaadaa9573a, e9011d2e37924b089a884c281e986cba, e2cff27c10884b188391fdb7904e7c26, ea56f1ebf83743dc9f4bce5c8b986f62, e4f8529d7229459aae25a80423c62cd7, a4b41ea5ece347b5b0fecf59693df62c, 4c327fc2fa084699a2f97818801843f9, 70552bae0f2d4b1bb7c3b113f03093cb, 8626221607c44b3b948b536dbb3e7567, 18ee4be46ca34c01b258ee654513c315, 74f486eb991140278cd2cd2cadecc1f1, 3376e4a67aec436f929e73c028017ea8, af05d81ddb9f47ad981ba47bf90b6a18, 68f07d061049483197d9e97cffec0e14, 44c0c1569a2546e4b7fa8c8200833e1c, 14de180e02ad4d978678281f580ae238, e152cf1baf594079a91ea597135793bd, 63ccc27351de47ebbd5b8619a53f56fd, c5878408351341df9f79d5f3c16dd316, a73df0c48d6f42ed9df0eb0f4fde8e89, 1c963cb659544238ad6dcc24f5acd429, 90c05ffbc4a54188aace213625993c6b, 2a054e9b2c3e41efbab2caff32741c90, 6bc50e06c03f4cbb88b0e8fd67cc09d3, f57b986061c441b18d9c109a3816b1ef, 7450c30c681247439fd68dc85136b2af, 91cc2903f40f412bb1b29ba850e4371c, f5fe35dde4e24517a2850006473e55c2, 9ba0378b495442a98de541a5d194ddaa, f07ceb0294b04fa89d86a029c6b868ca, 726d7aa0638e4ed18da86e30b182e39e, 297c757e9eaa4026baad0c45dd8e79f1, 139c8cf34b27410d9317c9279c8ec46d, 63ddc3fcabc640d19bb90ee068e180a1, 793081ad28de4c87ac99abf8b35d9e90, 02e33fd7b687440b86523d7e4cb2b854, d7b66ce0b7f4470bbe7f8a914b41d927, 25cf70e7bc9f4465a39bdf5584147f75, aae2325c5fe24755a4eb9b765936f1ae
- **Transparent secondary — Q4_0 p95 end-to-end latency reduction:** 2.769%; paired 95% bootstrap interval -18.448 to 49.453%. The interval crosses zero. This secondary outcome is always disclosed and cannot unlock final publication.  - Baseline: `a1-generic-q4-0`
  - Optimized: `a2-kleidiai-q4-0`
  - Formula: `(generic_q4_0_p95_ms - kleidiai_q4_0_p95_ms) / generic_q4_0_p95_ms * 100`
  - Source run IDs: b169922e39024a9bafda9693ecbff34d, 271ae569b28a448182781aaadaa9573a, e9011d2e37924b089a884c281e986cba, e2cff27c10884b188391fdb7904e7c26, ea56f1ebf83743dc9f4bce5c8b986f62, e4f8529d7229459aae25a80423c62cd7, a4b41ea5ece347b5b0fecf59693df62c, 4c327fc2fa084699a2f97818801843f9, 70552bae0f2d4b1bb7c3b113f03093cb, 8626221607c44b3b948b536dbb3e7567, 18ee4be46ca34c01b258ee654513c315, 74f486eb991140278cd2cd2cadecc1f1, 3376e4a67aec436f929e73c028017ea8, af05d81ddb9f47ad981ba47bf90b6a18, 68f07d061049483197d9e97cffec0e14, 44c0c1569a2546e4b7fa8c8200833e1c, 14de180e02ad4d978678281f580ae238, e152cf1baf594079a91ea597135793bd, 63ccc27351de47ebbd5b8619a53f56fd, c5878408351341df9f79d5f3c16dd316, a73df0c48d6f42ed9df0eb0f4fde8e89, 1c963cb659544238ad6dcc24f5acd429, 90c05ffbc4a54188aace213625993c6b, 2a054e9b2c3e41efbab2caff32741c90, 6bc50e06c03f4cbb88b0e8fd67cc09d3, f57b986061c441b18d9c109a3816b1ef, 7450c30c681247439fd68dc85136b2af, 91cc2903f40f412bb1b29ba850e4371c, f5fe35dde4e24517a2850006473e55c2, 9ba0378b495442a98de541a5d194ddaa, f07ceb0294b04fa89d86a029c6b868ca, 726d7aa0638e4ed18da86e30b182e39e, 297c757e9eaa4026baad0c45dd8e79f1, 139c8cf34b27410d9317c9279c8ec46d, 63ddc3fcabc640d19bb90ee068e180a1, 793081ad28de4c87ac99abf8b35d9e90, 02e33fd7b687440b86523d7e4cb2b854, d7b66ce0b7f4470bbe7f8a914b41d927, 25cf70e7bc9f4465a39bdf5584147f75, aae2325c5fe24755a4eb9b765936f1ae
- **Transparent secondary — Q4_0 median per-request throughput increase:** 2.857%; paired 95% bootstrap interval -3.226 to 10.491%. The interval crosses zero. This secondary outcome is always disclosed and cannot unlock final publication.  - Baseline: `a1-generic-q4-0`
  - Optimized: `a2-kleidiai-q4-0`
  - Formula: `(kleidiai_q4_0_median_rps - generic_q4_0_median_rps) / generic_q4_0_median_rps * 100`
  - Source run IDs: b169922e39024a9bafda9693ecbff34d, 271ae569b28a448182781aaadaa9573a, e9011d2e37924b089a884c281e986cba, e2cff27c10884b188391fdb7904e7c26, ea56f1ebf83743dc9f4bce5c8b986f62, e4f8529d7229459aae25a80423c62cd7, a4b41ea5ece347b5b0fecf59693df62c, 4c327fc2fa084699a2f97818801843f9, 70552bae0f2d4b1bb7c3b113f03093cb, 8626221607c44b3b948b536dbb3e7567, 18ee4be46ca34c01b258ee654513c315, 74f486eb991140278cd2cd2cadecc1f1, 3376e4a67aec436f929e73c028017ea8, af05d81ddb9f47ad981ba47bf90b6a18, 68f07d061049483197d9e97cffec0e14, 44c0c1569a2546e4b7fa8c8200833e1c, 14de180e02ad4d978678281f580ae238, e152cf1baf594079a91ea597135793bd, 63ccc27351de47ebbd5b8619a53f56fd, c5878408351341df9f79d5f3c16dd316, a73df0c48d6f42ed9df0eb0f4fde8e89, 1c963cb659544238ad6dcc24f5acd429, 90c05ffbc4a54188aace213625993c6b, 2a054e9b2c3e41efbab2caff32741c90, 6bc50e06c03f4cbb88b0e8fd67cc09d3, f57b986061c441b18d9c109a3816b1ef, 7450c30c681247439fd68dc85136b2af, 91cc2903f40f412bb1b29ba850e4371c, f5fe35dde4e24517a2850006473e55c2, 9ba0378b495442a98de541a5d194ddaa, f07ceb0294b04fa89d86a029c6b868ca, 726d7aa0638e4ed18da86e30b182e39e, 297c757e9eaa4026baad0c45dd8e79f1, 139c8cf34b27410d9317c9279c8ec46d, 63ddc3fcabc640d19bb90ee068e180a1, 793081ad28de4c87ac99abf8b35d9e90, 02e33fd7b687440b86523d7e4cb2b854, d7b66ce0b7f4470bbe7f8a914b41d927, 25cf70e7bc9f4465a39bdf5584147f75, aae2325c5fe24755a4eb9b765936f1ae

## Candidate summary

| Candidate | Stage | Backend | n | p50 ms | p95 ms | Quality | Safety | Peak RSS MB |
|---|---|---|---:|---:|---:|---:|---:|---:|
| a0-generic-q8 | reference | generic | 10 | 7280.23 | 10671.28 | 67.25 | 80.00 | 3396.7 |
| a1-generic-q4-0 | baseline | generic | 20 | 6111.74 | 7353.42 | 72.97 | 100.00 | 1940.4 |
| a2-kleidiai-q4-0 | kleidiai | kleidiai | 20 | 5941.62 | 7149.83 | 73.88 | 100.00 | 1941.1 |
| kleidiai-q4-0-t4-b128-u128-p1-c2048 | tuned | kleidiai | 20 | 5919.99 | 7049.28 | 73.88 | 100.00 | 1954.0 |
| kleidiai-q4-0-t4-b128-u64-p1-c2048 | tuned | kleidiai | 20 | 5967.49 | 7006.59 | 73.88 | 100.00 | 1942.1 |

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

The submitted API profile is strong-only. Its public boundary applies constrained triage JSON and
fails closed when schema, read-only tool policy, safety, or consistency validation fails. A4
weak/strong routing remains a future experiment unless calibration and the complete held-out gate
approve a measured multi-runtime profile.

## Limitations

- Results apply only to the recorded target, model files, runtime commit, and workload.
- The synthetic incident suite is not a general LLM capability benchmark.
- No energy or cloud-cost claim is made without a credible counter or supplied price.
- Fixture responses are excluded from every performance claim.

Generated at 2026-08-14T04:01:45.230902+00:00. The repository carries the sanitized formal rows in
`benchmark-results.json` plus `claims.json`; the attested release bundle carries the full redacted
`raw/` capture and integrity receipts.