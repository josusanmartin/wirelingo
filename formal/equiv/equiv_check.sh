#!/usr/bin/env bash
# RTL-vs-netlist equivalence check of a hardening run (docs/equivalence.md).
#
#   formal/equiv/equiv_check.sh [-j N] [--mutants] [--skip-simple] RUN [OUT]
#
# RUN: a hardening worktree (e.g. ~/protoemu-flow/protoemu-exp-fo6) or its
#      runs/<tag> directory. It is only read.
# OUT: a new work directory (default ~/protoemu-flow/equiv-<run>-<UTC time>).
#
# Needs python3 and yosys/yosys-abc (OSS CAD Suite: $OSS_CAD, default
# ~/tools/oss-cad-suite). The PDK cell models are taken from the run's
# resolved.json. Heavy: ~1 GB per job and about 5 minutes with -j 4 on runner1
# (20 cores); -j sets how many equiv_simple processes run at once.
# Exit status 0 only for a full proof (see the VERDICT line).
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
args=()
while [[ $# -gt 0 && $1 == -* ]]; do
  case $1 in
    -j) args+=("$1" "$2"); shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
[[ $# -ge 1 ]] || { sed -n '2,15p' "$0"; exit 2; }
run=$1
out=${2:-$HOME/protoemu-flow/equiv-$(basename "$(cd "$run" && pwd)")-$(date -u +%Y%m%dT%H%M%SZ)}
export PATH="${OSS_CAD:-$HOME/tools/oss-cad-suite}/bin:$PATH"
exec python3 "$here/equiv.py" "${args[@]}" "$run" "$out"
