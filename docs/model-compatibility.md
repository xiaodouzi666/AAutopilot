# Model and KleidiAI compatibility

The primary backend ablation deliberately uses **Q4_0**, not Q4_K_M. At the pinned
`llama.cpp` commit (`a94d563ed801d1da1b8c2432946de07d0231bb3d`), the official KleidiAI
implementation selects quantized kernels for `GGML_TYPE_Q4_0` and `GGML_TYPE_Q8_0`.
For another quantized tensor type it logs that no kernel is available and that the tensor
is not accelerated. The exact pinned strong Q4_0 file contains one such tensor: Q6_K
`output.weight`. AArch64 Autopilot permits only this disclosed fallback, and only after the
file SHA-256, byte size, and full parsed GGUF tensor-header inventory all match the registry.
Every additional or different fallback remains a failed optimized run.

Primary sources:

- [Pinned KleidiAI implementation](https://github.com/ggml-org/llama.cpp/blob/a94d563ed801d1da1b8c2432946de07d0231bb3d/ggml/src/ggml-cpu/kleidiai/kleidiai.cpp#L1840-L1860)
- [Official Qwen2.5 0.5B GGUF repository](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/tree/9217f5db79a29953eb74d5343926648285ec7e67)
- [Official Qwen2.5 1.5B GGUF repository](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/tree/91cad51170dc346986eccefdc2dd33a9da36ead9)

## Reviewed immutable files

| Role | Revision | Exact filename | Bytes | LFS SHA-256 | Parsed tensor histogram |
|---|---|---|---:|---|---|
| weak | `9217f5db79a29953eb74d5343926648285ec7e67` | `qwen2.5-0.5b-instruct-q4_0.gguf` | 428,730,208 | `7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed` | F32=121, Q4_0=169, Q8_0=1 |
| strong primary | `91cad51170dc346986eccefdc2dd33a9da36ead9` | `qwen2.5-1.5b-instruct-q4_0.gguf` | 1,066,227,232 | `dcd819ff094852c38faba6873d8ff0c9d51eadb2844539e52042ae5d647bbfdb` | F32=141, Q4_0=197, Q6_K=1 (`output.weight`, 1536×151936) |
| strong reference | `91cad51170dc346986eccefdc2dd33a9da36ead9` | `qwen2.5-1.5b-instruct-q8_0.gguf` | 1,894,532,128 | `d7efb072e7724d25048a4fda0a3e10b04bdef5d06b1403a1c93bd9f1240a63c8` | F32=141, Q8_0=198 |

The downloader resolves repository metadata at those revisions, verifies the official LFS
SHA-256 against the downloaded bytes, and writes the resolved revision, filename, size,
hash, license, tensor histogram, canonical full-inventory digest, reviewed fallback list, and
`kleidiai_compatible` flag to `artifacts/model-manifest.json`. Strict verification reparses
the local GGUF header instead of trusting the manifest. Model weights remain ignored and are
never committed.

## Acceptance rule

A KleidiAI row is eligible only when all of the following hold:

1. its manifest model is marked KleidiAI-compatible;
2. the record quantization matches the manifest and is `Q4_0` for the primary A1/A2 pair;
3. startup output contains the matching primary Q4 kernel marker;
4. a fallback warning is absent, except for the exact strong-model Q6_K `output.weight`
   warning backed by its pinned SHA/size and full-header inventory proof;
5. any additional or different unsupported-tensor warning is rejected;
6. CPU-only command and build-cache checks pass.
