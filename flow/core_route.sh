#!/bin/bash
# Quick routability benchmark for one core (pe_core + its FIFOs, IDX 0 so the
# line-unit ports are live), placed alone and stopped after global routing
# (~10 min). For comparing datapath changes without hardening the chip.
#
#   flow/core_route.sh NAME [SRC_DIR]
#   TOP=pe_line flow/core_route.sh NAME [SRC_DIR]     # another block alone
#
# SRC_DIR (default: this repo's src/) supplies pe_core.sv, pe_fifos.sv,
# pe_fifo.sv and pe_cg.sv. Runs in <parent of this repo>/pe-core-NAME and
# prints cell area, total global-route wirelength, the Metal2-4 usage and the
# slow-corner setup slack after the flow's timing repair (wire capacitance as
# measured, SRAM-like delays on instr and fetch_addr).
# The die (560 x 560 um, density 34 %) and routing layers (Metal2-Metal4,
# PDN stripes on Metal4) match the tile's conditions, not its shape.
set -euo pipefail
NAME=${1:?usage: flow/core_route.sh NAME [SRC_DIR]}
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
SRC=$(realpath "${2:-$REPO/src}")
W=$(dirname "$REPO")/pe-core-$NAME
rm -rf "$W"; mkdir -p "$W/src"
TOP=${TOP:-pe_core}
case $TOP in
  pe_core) FILES="pe_core.sv pe_fifos.sv pe_fifo.sv pe_cg.sv"; PARAMS='"IDX=0", "FIFO_LOG2=3"'; DIE=560 ;;
  pe_line) FILES="pe_line.sv"; PARAMS=''; DIE=330 ;;
  pe_hostport) FILES="pe_hostport.sv"; PARAMS=''; DIE=300 ;;
  *) echo "unknown TOP $TOP"; exit 2 ;;
esac
for f in $FILES; do cp "$SRC/$f" "$W/src/"; done
VFILES=$(for f in $FILES; do printf '"dir::%s", ' $f; done); VFILES=${VFILES%, }
cp "$REPO/src/protoemu.sdc" "$W/src/" 2>/dev/null || true
cd "$W"
# The fetch loop as in the chip: instr arrives after the SRAM's slow-corner
# clock->DOUT (5.55 ns) plus wire; fetch_addr needs the bank mux and setup.
cat > src/core.sdc <<EOF
create_clock -name clk -period 20 [get_ports clk]
$( [ $TOP = pe_hostport ] && echo 'create_clock -name sck -period 20 [get_ports sck]
set_clock_groups -asynchronous -group clk -group sck' )
set_input_delay 4 -clock clk [delete_from_list [all_inputs] [get_ports {clk sck instr*}]]
set_output_delay 1 -clock clk [delete_from_list [all_outputs] [get_ports fetch_addr*]]
$( [ $TOP = pe_core ] && echo "set_input_delay ${INSTR_DELAY:-6.5} -clock clk [get_ports instr*]
set_output_delay 2.5 -clock clk [get_ports fetch_addr*]" )
EOF
cat > src/config.json <<EOF
{
  "DESIGN_NAME": "$TOP",
  "VERILOG_FILES": [$VFILES],
  "SYNTH_PARAMETERS": [$PARAMS],
  "CLOCK_PORT": "clk",
  "CLOCK_PERIOD": 20,
  "PNR_SDC_FILE": "dir::core.sdc",
  "SIGNOFF_SDC_FILE": "dir::core.sdc",
  "FP_SIZING": "absolute",
  "DIE_AREA": [0, 0, $DIE, $DIE],
  "PL_TARGET_DENSITY_PCT": 34,
  "RT_MAX_LAYER": "Metal4",
  "FP_PDN_MULTILAYER": 0,
  "FP_PDN_VPITCH": 44.96,
  "FP_PDN_VWIDTH": 2.1,
  "FP_PDN_VSPACING": 3.52,
  "FP_PDN_VOFFSET": 6.15,
  "PNR_CORNERS": ["nom_typ_1p20V_25C", "nom_slow_1p08V_125C"],
  "LAYERS_RC": {"*": {"Metal1": {"res": 8.4375e-4, "cap": 1.42e-4}, "Metal2": {"res": 5.15e-4, "cap": 1.9e-4},
                      "Metal3": {"res": 5.15e-4, "cap": 1.9e-4}, "Metal4": {"res": 5.15e-4, "cap": 1.9e-4}}},
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
  echo "slow setup slack after repair: $(grep -h 'timing__setup__ws__corner:nom_slow' runs/r/*stamidpnr*/*.log 2>/dev/null | tail -1 | grep -oE '[-0-9.]+$')"
} | tee result.txt
exit $rc
