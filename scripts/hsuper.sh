#!/usr/bin/env bash
# hsuper.sh — 受监督派发：按声明流程执行，并在 worker 出错时做有界恢复。见 --help。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$script_dir/hsuper.py" "$@"
