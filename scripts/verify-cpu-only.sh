#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/verify-cpu-only.sh [--build-only]

Verify that both CMake caches disable reviewed GPU backends and differ only by
GGML_CPU_KLEIDIAI. The default additionally requires measured runtime rows
whose command/log evidence passed the Python CPU-only and backend validators.
EOF
}

build_only=false
case "${1:-}" in
  "") ;;
  --build-only) build_only=true ;;
  -h|--help) usage; exit 0 ;;
  *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$project_root"

generic_cache=build/llama-generic/CMakeCache.txt
kleidiai_cache=build/llama-kleidiai/CMakeCache.txt
manifest=artifacts/build-manifest.json

for required_path in "$generic_cache" "$kleidiai_cache" "$manifest"; do
  if [[ ! -f "$required_path" ]]; then
    printf 'Missing build proof: %s\n' "$required_path" >&2
    exit 2
  fi
done

uv run --frozen --no-editable python - "$generic_cache" "$kleidiai_cache" "$manifest" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

from a64pilot.build.cmake import COMMON_DEFINITIONS
from a64pilot.build.verify_backend import parse_cmake_cache

generic_path, kleidiai_path, manifest_path = map(Path, sys.argv[1:])
generic = parse_cmake_cache(generic_path.read_text(encoding="utf-8", errors="replace"))
kleidiai = parse_cmake_cache(kleidiai_path.read_text(encoding="utf-8", errors="replace"))

off_keys = {
    key
    for key, value in COMMON_DEFINITIONS.items()
    if key.startswith("GGML_") and value == "OFF"
}
false_values = {"OFF", "FALSE", "0", "NO"}
errors: list[str] = []
for label, cache in (("generic", generic), ("kleidiai", kleidiai)):
    for key in sorted(off_keys):
        if key not in cache:
            errors.append(f"{label} cache missing {key}")
        elif cache[key].strip().upper() not in false_values:
            errors.append(f"{label} cache enables {key}={cache[key]}")

if generic.get("GGML_CPU_KLEIDIAI", "").upper() not in false_values:
    errors.append("generic cache does not disable GGML_CPU_KLEIDIAI")
if kleidiai.get("GGML_CPU_KLEIDIAI", "").upper() not in {"ON", "TRUE", "1", "YES"}:
    errors.append("KleidiAI cache does not enable GGML_CPU_KLEIDIAI")

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
builds = {row.get("backend"): row for row in payload.get("variants", [])}
if set(builds) != {"generic", "kleidiai"}:
    errors.append("build manifest must contain exactly generic and kleidiai variants")
else:
    commits = {str(row.get("source_commit", "")) for row in builds.values()}
    if len(commits) != 1 or len(next(iter(commits), "")) != 40:
        errors.append("builds do not share one full pinned source commit")
    def definitions(row: dict[str, object]) -> dict[str, str]:
        values: dict[str, str] = {}
        for flag in row.get("cmake_flags", []):
            if isinstance(flag, str) and flag.startswith("-D") and "=" in flag:
                key, value = flag[2:].split("=", 1)
                values[key] = value
        return values

    generic_defs = definitions(builds["generic"])
    kleidiai_defs = definitions(builds["kleidiai"])
    keys = set(generic_defs) | set(kleidiai_defs)
    unfair = sorted(
        key
        for key in keys
        if key != "GGML_CPU_KLEIDIAI" and generic_defs.get(key) != kleidiai_defs.get(key)
    )
    if unfair:
        errors.append("unfair CMake definition differences: " + ", ".join(unfair))
    if generic_defs.get("GGML_CPU_KLEIDIAI") != "OFF":
        errors.append("build manifest generic variant does not disable KleidiAI")
    if kleidiai_defs.get("GGML_CPU_KLEIDIAI") != "ON":
        errors.append("build manifest optimized variant does not enable KleidiAI")

result = {
    "build_configuration_cpu_only": not errors,
    "same_pinned_commit": not any("commit" in item for item in errors),
    "intended_backend_delta_only": not any("unfair" in item for item in errors),
    "errors": errors,
}
print(json.dumps(result, indent=2, sort_keys=True))
if errors:
    raise SystemExit(2)
PY

if [[ "$build_only" == true ]]; then
  printf 'Build configuration verified. Runtime CPU-only proof was not requested.\n'
  exit 0
fi

uv run --frozen --no-editable a64pilot verify-backends
printf 'Build configuration and measured runtime backend proofs verified.\n'
