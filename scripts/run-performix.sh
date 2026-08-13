#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run-performix.sh [PROFILER_COMMAND [ARG ...]]

Performix is optional supporting evidence. Pass an already configured profiler
command explicitly. It must write these files under artifacts/performix (or
$A64PILOT_PERFORMIX_DIR):

  generic-hotspots.json
  kleidiai-hotspots.json
  summary.md

With no command, the script writes UNAVAILABLE.md and exits 2. It never invents
hotspots or converts an unavailable integration into benchmark evidence.
EOF
}

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$project_root"

output_dir="${A64PILOT_PERFORMIX_DIR:-artifacts/performix}"
mkdir -p "$output_dir"
unavailable_file="$output_dir/UNAVAILABLE.md"

write_unavailable() {
  local reason="$1"
  {
    printf '# Arm Performix evidence unavailable\n\n'
    printf 'Status: **UNAVAILABLE**\n\n'
    printf 'Reason: %s\n\n' "$reason"
    printf 'This status is not benchmark evidence and supports no optimization claim.\n'
  } >"$unavailable_file"
}

if (($# == 0)); then
  write_unavailable \
    'No configured profiler command was supplied to scripts/run-performix.sh.'
  printf 'Performix is unavailable; recorded %s\n' "$unavailable_file" >&2
  exit 2
fi

profiler_executable="$1"
if [[ "$profiler_executable" == */* ]]; then
  [[ -x "$profiler_executable" ]] || {
    write_unavailable 'The explicitly supplied profiler executable was not executable.'
    printf 'Profiler executable is not executable.\n' >&2
    exit 2
  }
elif ! command -v "$profiler_executable" >/dev/null 2>&1; then
  write_unavailable 'The explicitly supplied profiler executable was not found on PATH.'
  printf 'Profiler executable was not found on PATH.\n' >&2
  exit 2
fi

export A64PILOT_PERFORMIX_DIR="$output_dir"
set +e
"$@"
profiler_status=$?
set -e
if [[ "$profiler_status" -ne 0 ]]; then
  write_unavailable "The supplied profiler command exited with status $profiler_status."
  printf 'Profiler command failed with status %s.\n' "$profiler_status" >&2
  exit "$profiler_status"
fi

required_outputs=(generic-hotspots.json kleidiai-hotspots.json summary.md)
for output_name in "${required_outputs[@]}"; do
  if [[ ! -s "$output_dir/$output_name" ]]; then
    write_unavailable "The profiler completed but did not produce required file $output_name."
    printf 'Missing required Performix output: %s\n' "$output_name" >&2
    exit 2
  fi
done

uv run --frozen --no-editable python - "$output_dir" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
for name in ("generic-hotspots.json", "kleidiai-hotspots.json"):
    json.loads((root / name).read_text(encoding="utf-8"))
summary = (root / "summary.md").read_text(encoding="utf-8")
if re.search(r"(?i)\b(?:todo|tbd|placeholder|insert result)\b", summary):
    raise SystemExit("Performix summary contains an unresolved placeholder")
PY

uv run --frozen --no-editable python scripts/redact-artifacts.py --check "$output_dir"
if [[ -f "$unavailable_file" ]]; then
  rm -f -- "$unavailable_file"
fi
printf 'Validated optional Performix evidence under %s\n' "$output_dir"
