#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/build-llama.sh [all|generic|kleidiai] [options]

Options:
  --jobs N          Native build parallelism (default: 4).
  --dry-run         Print the immutable checkout and CMake plans only.
  --allow-non-arm   Permit a development build outside Arm64 Linux.
  -h, --help        Show this help.
EOF
}

variant=all
jobs=4
dry_run=false
allow_non_arm=false

if (($#)) && [[ "$1" =~ ^(all|generic|kleidiai)$ ]]; then
  variant="$1"
  shift
fi
while (($#)); do
  case "$1" in
    --jobs)
      [[ $# -ge 2 ]] || { printf '%s\n' '--jobs requires a value' >&2; exit 2; }
      jobs="$2"
      shift 2
      ;;
    --dry-run) dry_run=true; shift ;;
    --allow-non-arm) allow_non_arm=true; shift ;;
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

if [[ "$dry_run" != true ]]; then
  host_system="$(uname -s)"
  host_arch="$(uname -m)"
  if [[ "$host_system" != Linux || ! "$host_arch" =~ ^(aarch64|arm64)$ ]]; then
    if [[ "$allow_non_arm" != true ]]; then
      printf 'Evidence builds require Linux arm64/aarch64; found %s %s.\n' \
        "$host_system" "$host_arch" >&2
      exit 2
    fi
    printf 'DEVELOPMENT ONLY: building on non-target host %s %s.\n' \
      "$host_system" "$host_arch" >&2
  fi
  "$script_dir/install-system-deps.sh" --check
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required before building.\n' >&2
  exit 2
fi

command=(uv run --frozen --no-editable a64pilot build "$variant" --jobs "$jobs")
if [[ "$dry_run" == true ]]; then
  command+=(--dry-run)
fi
"${command[@]}"

if [[ "$dry_run" != true && "$variant" == all ]]; then
  "$script_dir/verify-cpu-only.sh" --build-only
fi
