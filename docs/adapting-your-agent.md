# Adapting another agent workload

1. Copy the incident case schema and replace user prompts with your domain fixtures.
2. Define deterministic expected labels, acceptable/required read-only tools, prohibited
   actions, and escalation behavior.
3. Preserve a calibration/held-out split; do not expose held-out labels to the router.
4. Implement a scorer that returns quality and safety independently.
5. Reuse the same prompt/constraint across generic and optimized candidates.
6. Set the maximum quality drop and optional p95/RSS constraints in
   `configs/quality-gate.yaml`.
7. Run the bounded search. Inspect rejected candidates as well as the selected profile.

Avoid tasks whose correctness cannot be scored consistently. The optimizer needs a meaningful
hard constraint, not a subjective “looks good” check.
