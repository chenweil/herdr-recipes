#!/usr/bin/env bash
# hwait.sh — wait for a delegated agent's completion marker or lifecycle change. See --help.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$script_dir/hwait.py" "$@"
