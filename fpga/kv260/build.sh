#!/usr/bin/env bash
# Build the protoemu KV260 app on a remote Vivado host and fetch it.
#
#   fpga/kv260/build.sh [--cores N] [clk_div]
#
# --cores: 3 (default: src/project.v's PE_NCORES, the 8x4 package) or 2 (its
# fallback); each core has its own 256-word bank, FIFOs are 8 deep. clk_div:
# 24 = 50 MHz (default), 25 = 48 MHz, 30 = 40 MHz; no other clock is planned
# (kv260_top.v's SPI device clock is CLK_DIV / 8 VCO periods, truncated:
# exactly 8x protoemu's clock only at 24, and build.tcl cuts its crossings
# otherwise).
#
# The RTL inputs must be committed (ALLOW_DIRTY=1 overrides): the bitstream
# records their revision (build_id.py, read back by host/bringup_kria.py).
# Env: PMOD_DRIVE (4, 8, 12, 16 mA; default the .xdc's), VIVADO_THREADS (default 4),
# VIVADO_NICE (default 10: the remote host is shared), VIVADO_HOST (default runner1),
# REMOTE_DIR (default protoemu-flow/fpga, relative to the remote home), VIVADO_SETTINGS
# (default ~/Xilinx/2026.1/Vivado/settings64.sh on the remote), OUT_DIR (default
# fpga/kv260/out), FETCH (default 1; 0 leaves the bitstream on the remote host and
# fetches the reports only). Output: OUT_DIR (protoemu.bit.bin, reports,
# build_info.json with the core count and clk_div; install.sh adds the overlay),
# not tracked by git.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
cores=
while [ $# -gt 0 ]; do
  case "$1" in
    --cores) [ $# -ge 2 ] || { echo "--cores needs a value" >&2; exit 1; }; cores=$2; shift 2;;
    --cores=*) cores=${1#--cores=}; shift;;
    -*) echo "unknown option $1" >&2; exit 1;;
    *) break;;
  esac
done
[ $# -le 1 ] || { echo "usage: build.sh [--cores 2|3] [clk_div]" >&2; exit 1; }
clk_div=${1:-24}
asic_cores=$(sed -n 's/^`define PE_NCORES \([0-9][0-9]*\)$/\1/p' "$root/src/project.v")
[ -n "$asic_cores" ] || { echo "src/project.v: no PE_NCORES default" >&2; exit 1; }
cores=${cores:-$asic_cores}
case "$cores" in 2|3) ;; *) echo "--cores must be 2 or 3 (the 8x4 package and its fallback)" >&2; exit 1;; esac
case "$clk_div" in 24|25|30) ;; *) echo "clk_div must be 24 (50 MHz), 25 (48 MHz) or 30 (40 MHz)" >&2; exit 1;; esac
host=${VIVADO_HOST:-runner1}
rdir=${REMOTE_DIR:-protoemu-flow/fpga}
settings=${VIVADO_SETTINGS:-'~/Xilinx/2026.1/Vivado/settings64.sh'}
nice_n=${VIVADO_NICE:-10}
case "$nice_n" in ''|*[!0-9]*) echo "VIVADO_NICE must be 0..19" >&2; exit 1;; esac
out=${OUT_DIR:-$here/out}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# the bitstream carries its RTL revision (pe_lab, build_id.py): refuse a dirty one
dirty=$(python3 "$here/build_id.py" info | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["dirty"]))')
if [ "$dirty" = 1 ] && [ "${ALLOW_DIRTY:-0}" != 1 ]; then
  echo "RTL inputs have uncommitted changes (git status src flow/fpga_profile.py fpga/kv260);" >&2
  echo "commit them, or ALLOW_DIRTY=1 for a bitstream marked dirty" >&2
  exit 1
fi
# the chip: $cores cores, a 256-word bank each, 8-deep FIFOs
python3 "$root/flow/fpga_profile.py" --output "$stage/rtl" --cores "$cores" --fifo-log2 3
cp "$here/build.tcl" "$here/kv260_top.v" "$here/pe_spim.v" "$here/pe_lab.v" "$here/kv260.xdc" "$stage/"
# PMOD_DRIVE=12: stronger PMOD pins for 10BASE-T TX (build_info.json records it)
pmod_drive=${PMOD_DRIVE:-}
if [ -n "$pmod_drive" ]; then
  case "$pmod_drive" in 4|8|12|16) ;; *) echo "PMOD_DRIVE must be 4, 8, 12 or 16" >&2; exit 1;; esac
  echo "set_property DRIVE $pmod_drive [get_ports {pmod[*]}]" >> "$stage/kv260.xdc"
fi
mkdir -p "$out"
python3 "$here/build_id.py" vh "$stage" "$stage/build_id.vh" "$stage/build_info.json" "$clk_div"
ssh "$host" "rm -rf $rdir/build && mkdir -p $rdir/build"
rsync -a "$stage/" "$host:$rdir/build/"
ssh "$host" "cd $rdir/build && source $settings >/dev/null && \
  VIVADO_THREADS=${VIVADO_THREADS:-4} nice -n $nice_n vivado -mode batch -nojournal -log vivado.log -source build.tcl -tclargs rtl/src out $clk_div >/dev/null && \
  printf 'all: { [destination_device = pl] out/protoemu.bit }\n' > bit.bif && \
  nice -n $nice_n bootgen -image bit.bif -arch zynqmp -process_bitstream bin -w on >/dev/null && \
  grep PROTOEMU_RESULT vivado.log; grep \"CRITICAL WARNING\" vivado.log | sort | uniq -c || true"

rm -f "$out"/*.rpt "$out/protoemu.bit.bin"
cp "$stage/build_info.json" "$out/"
rsync -a "$host:$rdir/build/out/"'*.rpt' "$host:$rdir/build/vivado.log" "$out/"
if [ "${FETCH:-1}" = 1 ]; then
  rsync -a "$host:$rdir/build/out/protoemu.bit.bin" "$out/"
  echo "built $out/protoemu.bit.bin ($cores cores, clk_div $clk_div): $(tr -d '\n ' < "$out/build_info.json")"
else
  echo "built $host:$rdir/build/out/protoemu.bit.bin ($cores cores, clk_div $clk_div; not fetched): $(tr -d '\n ' < "$out/build_info.json")"
fi
