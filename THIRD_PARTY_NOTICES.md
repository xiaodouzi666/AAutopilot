# Third-party notices

AArch64 Autopilot is Apache-2.0 licensed. It orchestrates, but does not vendor or
redistribute, the following separately licensed projects and model weights.

| Component | Source | License / notice |
|---|---|---|
| `llama.cpp` | https://github.com/ggml-org/llama.cpp | MIT; fetched and pinned by `make build` |
| KleidiAI | https://github.com/ARM-software/kleidiai | Apache-2.0; pulled by the pinned `llama.cpp` build when enabled |
| Qwen2.5 0.5B Instruct GGUF | https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF | Apache-2.0; downloaded locally, never committed |
| Qwen2.5 1.5B Instruct GGUF | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF | Apache-2.0; downloaded locally, never committed |

Python dependencies retain their respective licenses. Exact resolved versions are
recorded in `uv.lock`. Model filenames, revisions, sizes, and SHA-256 checksums are
recorded in the generated `artifacts/model-manifest.json`.

Arm, KleidiAI, Qwen, Hugging Face, and other names are used only to identify the
upstream technologies. No trademark endorsement is claimed.
