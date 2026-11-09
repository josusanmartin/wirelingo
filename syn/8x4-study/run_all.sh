#!/bin/sh
# Every synthesis row of docs/8x4-study.md, in order (about 3 minutes):
#   PATH=<oss-cad-suite>/bin:$PATH syn/8x4-study/run_all.sh > syn/8x4-study/results.txt
# Prototype RTL for the study, not a design change.
set -e
cd "$(dirname "$0")/../.."
S=syn/8x4-study/synth_chip.sh
echo "# syn/8x4-study/run_all.sh: chip-level yosys+abc, IHP SG13CMOS5L typ, SRAMs as black boxes; area in um^2"
echo "# $(yosys -V)"
echo "## (a) core count (8-deep FIFOs)"
for n in 2 3 4; do sh $S $n; done
echo "## control: the prototype files with no candidate enabled (= the 2-core row, within noise)"
sh $S 2 V8_NONE
sh $S 2 X_NONE
echo "## (a) core count, 4-deep FIFOs (PE_FIFO_LOG2=2)"
for n in 2 3 4; do sh $S $n PE_FIFO_LOG2=2; done
echo "## (c) a line unit for core 1"
sh $S 2 X_LINE1 V8_LINE1
sh $S 3 X_LINE1 V8_LINE1
echo "## (d) the v3 cheap bundle, (e) CRC-32 and the multi-bit tap, 2 cores"
sh $S 2 V3_CMPW V3_CMPW_SPLIT V3_NT V3_TBL V3_TBL_OSR V3_BANKREL V3_TOG
sh $S 2 V3_CRC32
sh $S 2 V3_MULTI
sh $S 2 V3_CRC32 V3_MULTI
sh $S 2 V3_CRC32 V8_CRCW
echo "## (f) data RAM / registers, (g) other core additions, 2 cores"
sh $S 2 V8_DRAM
sh $S 2 V8_XCH
sh $S 2 V8_TICK
sh $S 2 V8_PAR
sh $S 2 V8_TXCLR
sh $S 2 V8_SHV
echo "## (g) chip-level: two PWM / clock outputs"
sh $S 2 X_PWM
echo "## packages (bundle + any-width CRC + tick counter + TX clear + parity = PKG)"
B="V3_CMPW V3_CMPW_SPLIT V3_NT V3_TBL V3_TBL_OSR V3_BANKREL V3_TOG"
PKG="$B V3_CRC32 V8_CRCW V8_TICK V8_TXCLR V8_PAR"
sh $S 2 $PKG
sh $S 3 $PKG
sh $S 3 $PKG X_LINE1 V8_LINE1
sh $S 3 $PKG X_LINE1 V8_LINE1 X_PWM
sh $S 3 $B V3_CRC32 V8_CRCW V8_TICK
sh $S 4 $PKG PE_FIFO_LOG2=2
