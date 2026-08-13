#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install-system-deps.sh [--check]

Install the reviewed native build prerequisites on Ubuntu/Debian, or use
--check to verify that they already exist without changing the host.
EOF
}

mode=install
case "${1:-}" in
  "") ;;
  --check) mode=check ;;
  -h|--help) usage; exit 0 ;;
  *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
esac

required_commands=(cc c++ cmake git ninja pkg-config python3)

check_commands() {
  local missing=()
  local command_name
  for command_name in "${required_commands[@]}"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      missing+=("$command_name")
    fi
  done
  if ((${#missing[@]} > 0)); then
    printf 'Missing required commands: %s\n' "${missing[*]}" >&2
    return 2
  fi
  printf 'Native prerequisites are available: %s\n' "${required_commands[*]}"
}

if [[ "$mode" == check ]]; then
  check_commands
  exit
fi

if [[ "$(uname -s)" != Linux ]]; then
  printf 'Automatic system dependency installation supports Ubuntu/Debian Linux only.\n' >&2
  exit 2
fi
if ! command -v apt-get >/dev/null 2>&1; then
  printf 'apt-get is unavailable; install the packages listed in this script manually.\n' >&2
  exit 2
fi

apt_prefix=()
if [[ "$(id -u)" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    printf 'Root privileges or sudo are required to install system packages.\n' >&2
    exit 2
  fi
  apt_prefix=(sudo)
fi

export DEBIAN_FRONTEND=noninteractive
"${apt_prefix[@]}" apt-get update
"${apt_prefix[@]}" apt-get install -y --no-install-recommends \
  build-essential \
  ca-certificates \
  cmake \
  curl \
  git \
  jq \
  ninja-build \
  pkg-config \
  python3 \
  python3-dev \
  python3-venv

check_commands
