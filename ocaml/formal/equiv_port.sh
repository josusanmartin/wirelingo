#!/usr/bin/env bash
# Formal equivalence of the Hardcaml port against src/ (docs/hardcaml-port.md).
#
#   ocaml/formal/equiv_port.sh [-j N] [--mutants] [--skip-simple] [--build B] OUT
#
# Generates the Verilog with gen_rtl (cd ocaml && dune build first) and proves it
# equal to src/ with yosys and ABC. B: tapeout (default; project.v's own 3 cores
# and 3 banks) or fallback (2 and 2: src/ read with PE_NCORES=2 PE_NBANKS=2). OUT: a new work directory. Needs python3 and
# OSS CAD Suite ($OSS_CAD, default ~/tools/oss-cad-suite). Exit status 0 only for
# a full proof (see the VERDICT line).
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
args=()
build=tapeout
while [[ $# -gt 0 && $1 == -* ]]; do
  case $1 in
    -j) args+=("$1" "$2"); shift 2 ;;
    --build) build=$2; args+=("$1" "$2"); shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
[[ $# -eq 1 ]] || { sed -n '2,10p' "$0"; exit 2; }
out=$1
export PATH="${OSS_CAD:-$HOME/tools/oss-cad-suite}/bin:$PATH"
gen=$(mktemp -d)
"$here/../_build/default/bin/gen_rtl.exe" "$build" "$gen"
python3 "$here/equiv_port.py" "${args[@]}" "$gen" "$out"
