# Architecture

```mermaid
flowchart LR
  U[Developer or judge] --> C[a64pilot CLI]
  C --> D[Arm hardware doctor]
  C --> B[Generic and KleidiAI builds]
  C --> T[Bounded staged tuner]
  B --> G[Generic llama.cpp CPU]
  B --> K[KleidiAI llama.cpp CPU]
  T --> Q[Objective quality and safety gate]
  Q --> P[Measured profile]
  P --> A[OpenAI-compatible proxy]
  A --> R[Transparent complexity router]
  R --> W[Weak model]
  R --> S[Strong model]
  W --> V[Schema and safety validator]
  V -->|pass| A
  V -->|fail or escalate| S
  D --> E[(Raw evidence store)]
  T --> E
  E --> H[Offline HTML and Markdown report]
```

The runtime and report layers are intentionally separate. A model response can demonstrate
API behavior in fixture mode, but only measured, CPU-only rows with verified binary/model
provenance enter the claim generator.
