# AArch64 Autopilot Hardware Doctor

- Architecture: `aarch64`
- Operating system: `Darwin`
- Kernel: `25.5.0`
- CPU: `Apple M4 Pro`
- Logical CPUs available: 14
- Physical cores: 14
- Evidence-backed Arm features: dotprod, i8mm, sme, sme2, bf16, fp16
- Real benchmark eligible: no

## Limitations

- macOS does not expose Linux-style CPU affinity
- final service benchmark mode requires Linux on Arm64
