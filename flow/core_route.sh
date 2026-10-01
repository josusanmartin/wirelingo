#!/bin/bash
# Quick routability benchmark for one core (pe_core + its FIFOs, IDX 0 so the
# line-unit ports are live), placed alone and stopped after global routing
# (~10 min). For comparing datapath changes without hardening the chip.
#
#   flow/core_route.sh NAME [SRC_DIR]
#
# SRC_DIR (default: this repo's src/) supplies pe_core.sv, pe_fifos.sv,
# pe_fifo.sv and pe_cg.sv. Runs in <parent of this repo>/pe-core-NAME and
# prints cell area, total global-route wirelength and the Metal2-4 usage.
# The die (560 x 560 um, density 34 %) and routing layers (Metal2-Metal4,
# PDN stripes on Metal4) match the tile's conditions, not its shape.
set -euo pipefail
NAME=${1:?usage: flow/core_route.sh NAME [SRC_DIR]}
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
SRC=$(realpath "${2:-$REPO/src}")
W=$(dirname "$REPO")/pe-core-$NAME
rm -rf "$W"; mkdir -p "$W/src"
cp "$SRC"/{pe_core.sv,pe_fifos.sv,pe_fifo.sv,pe_cg.sv} "$W/src/"
cp "$REPO/src/protoemu.sdc" "$W/src/" 2>/dev/null || true
cd "$W"
cat > src/core.sdc <<'EOF'
create_clock -name clk -period 20 [get_ports clk]
set_input_delay 4 -clock clk [delete_from_list [all_inputs] [get_ports clk]]
set_output_delay 4 -clock clk [all_outputs]
EOF
cat > src/config.json <<'EOF'
{
  "DESIGN_NAME": "pe_core",
  "VERILOG_FILES": ["dir::pe_core.sv", "dir::pe_fifos.sv", "dir::pe_fifo.sv", "dir::pe_cg.sv"],
  "SYNTH_PARAMETERS": ["IDX=0", "FIFO_LOG2=3"],
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 20,
  "PNR_SDC_FILE": "dir::core.sdc",
  "SIGNOFF_SDC_FILE": "dir::core.sdc",
  "FP_SIZING": "absolute",
  "DIE_AREA": [0, 0, 560, 560],
  "PL_TARGET_DENSITY_PCT": 34,
  "RT_MAX_LAYER": "Metal4",
  "FP_PDN_MULTILAYER": 0,
  "FP_PDN_VPITCH": 44.96,
  "FP_PDN_VWIDTH": 2.1,
  "FP_PDN_VSPACING": 3.52,
  "FP_PDN_VOFFSET": 6.15,
  "PNR_CORNERS": ["nom_typ_1p20V_25C"],
  "GRT_ALLOW_CONGESTION": 1,
  "GRT_ADJUSTMENT": 0.15,
  "RUN_LINTER": 0,
  "OPENROAD_THREADS": 8
}
EOF
export PATH=$REPO/.venv-flow/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk}
mkdir -p runs/r
rc=0
python -m librelane --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized --pdk ihp-sg13cmos5l --manual-pdk \
  --run-tag r --force-run-dir runs/r --to OpenROAD.GlobalRouting src/config.json > exp.log 2>&1 || rc=$?
{
  echo "$NAME (exit $rc)"
  grep -h "Chip area for module" runs/r/*-yosys-synthesis/*.log 2>/dev/null | tail -1 || true
  grep -E "GPL-0019" exp.log | tail -1 || true
  grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total " | tail -4 || true
  grep -E "GRT-0018|Total wirelength" exp.log | tail -1 || true
} | tee result.txt
exit $rc
