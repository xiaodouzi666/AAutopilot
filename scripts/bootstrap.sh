#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap.sh [options]

Options:
  --install-system-deps  Install Ubuntu/Debian native prerequisites.
  --skip-build           Sync Python dependencies but do not build llama.cpp.
  --skip-models          Do not download model weights.
  --allow-non-arm        Permit source-development setup on a non-Arm/Linux host.
  --jobs N               Native build parallelism (default: 4).
  -h, --help             Show this help.

Final measurements still require Linux on arm64/aarch64. --allow-non-arm does
not make another host eligible and is recorded only as a development escape hatch.
EOF
}

install_system_deps=false
skip_build=false
skip_models=false
allow_non_arm=false
jobs=4

while (($#)); do
  case "$1" in
    --install-system-deps) install_system_deps=true; shift ;;
    --skip-build) skip_build=true; shift ;;
    --skip-models) skip_models=true; shift ;;
    --allow-non-arm) allow_non_arm=true; shift ;;
    --jobs)
      [[ $# -ge 2 ]] || { printf '%s\n' '--jobs requires a value' >&2; exit 2; }
      jobs="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
  printf 'Build jobs must be a positive integer, got: %s\n' "$jobs" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
cd "$project_root"

host_system="$(uname -s)"
host_arch="$(uname -m)"
if [[ "$host_system" != Linux || ! "$host_arch" =~ ^(aarch64|arm64)$ ]]; then
  if [[ "$allow_non_arm" != true ]]; then
    printf 'Bootstrap for measured runs requires Linux arm64/aarch64; found %s %s.\n' \
      "$host_system" "$host_arch" >&2
    printf 'For source-only development, rerun with --allow-non-arm --skip-build --skip-models.\n' >&2
    exit 2
  fi
  printf 'DEVELOPMENT ONLY: allowing non-target host %s %s; no measurements are eligible.\n' \
    "$host_system" "$host_arch" >&2
fi

if [[ "$install_system_deps" == true ]]; then
  "$script_dir/install-system-deps.sh"
elif [[ "$skip_build" != true ]]; then
  "$script_dir/install-system-deps.sh" --check
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required. Install it from https://docs.astral.sh/uv/ and rerun.\n' >&2
  exit 2
fi

uv sync --frozen --extra dev --no-editable --reinstall-package aarch64-autopilot
uv run --frozen --no-editable a64pilot doctor

if [[ "$skip_build" != true ]]; then
  "$script_dir/build-llama.sh" all --jobs "$jobs"
fi
if [[ "$skip_models" != true ]]; then
  uv run --frozen --no-editable python "$script_dir/download-models.py" --minimum
  uv run --frozen --no-editable a64pilot models verify
fi

if [[ "$skip_build" == true || "$skip_models" == true ]]; then
  printf 'Partial/source-development bootstrap completed. Run make fixture-smoke; real smoke still requires the skipped inputs.\n'
else
  printf 'Target bootstrap completed. Run make smoke, then make optimize.\n'
fi
