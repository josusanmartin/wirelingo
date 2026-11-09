#!/bin/sh
# Chip-level standard-cell area for the 8x4 study (docs/8x4-study.md):
# yosys + abc to the IHP SG13CMOS5L typ library, the SRAM macros as black
# boxes (their area is the LEF outline, added in the doc), no placement,
# buffering or clock tree (fo6's hardened stdcell area is 1.276x this
# script's figure for the taped-out RTL).
#
#   syn/8x4-study/synth_chip.sh NCORES [DEFINE ...]
#
# NCORES 1-4 sets PE_NCORES and PE_NBANKS (8-deep FIFOs, as taped out). A
# define starting with X_ selects the chip prototype syn/8x4-study/pe_chip_x.sv
# instead of src/pe_chip.sv; one starting with V3_ or V8_ selects the core
# prototype syn/8x4-study/pe_core_x.sv instead of src/pe_core.sv. Prints
# cells, flops, latches and area, then the main modules' own areas.
# Prototype RTL for the study, not a design change.
set -e
cd "$(dirname "$0")/../.."
NC=${1:?usage: synth_chip.sh NCORES [DEFINE ...]}; shift
LIB=${LIB:-$HOME/ttpdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib}
YOSYS=${YOSYS:-yosys}
D="-DPE_NCORES=$NC -DPE_NBANKS=$NC"
CHIP=src/pe_chip.sv
CORE=src/pe_core.sv
for d in "$@"; do
  D="$D -D$d"
  case $d in X_*) CHIP=syn/8x4-study/pe_chip_x.sv ;; V3_*|V8_*) CORE=syn/8x4-study/pe_core_x.sv ;; esac
done
OUT=$(mktemp)
$YOSYS -q -p "read_verilog -lib src/macros/RM_IHPSG13_1P_256x32_c2_bm_bist.v;
  read_verilog -sv -Isrc $D src/project.v $CHIP $CORE src/pe_fifos.sv src/pe_fifo.sv src/pe_cg.sv src/pe_imem.sv src/pe_hostport.sv src/pe_line.sv;
  hierarchy -top tt_um_josusanmartin_protoemu; synth -top tt_um_josusanmartin_protoemu;
  dfflibmap -liberty $LIB; abc -liberty $LIB; opt_clean;
  tee -q -o $OUT stat -liberty $LIB" >/dev/null
python3 "$(dirname "$0")/stat_summary.py" "$OUT" "$NC $*"
rm -f "$OUT"
