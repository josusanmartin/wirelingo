#!/usr/bin/env bash
# Build the protoemu KV260 app on a remote Vivado host and fetch it.
#
#   fpga/kv260/build.sh [clk_div]        # 24 = 50 MHz (default), 25 = 48 MHz
#
# Env: VIVADO_HOST (default runner1), REMOTE_DIR (default protoemu-flow/fpga,
# relative to the remote home), VIVADO_SETTINGS (default
# ~/Xilinx/2026.1/Vivado/settings64.sh on the remote). Output: fpga/kv260/out/
# (protoemu.bit.bin, reports; install.sh adds the overlay), not tracked by git.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
clk_div=${1:-24}
host=${VIVADO_HOST:-runner1}
rdir=${REMOTE_DIR:-protoemu-flow/fpga}
settings=${VIVADO_SETTINGS:-'~/Xilinx/2026.1/Vivado/settings64.sh'}
out=$here/out
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# tapeout configuration: 2 cores, 8-deep FIFOs
python3 "$root/flow/fpga_profile.py" --output "$stage/rtl" --cores 2 --fifo-log2 3
cp "$here/build.tcl" "$here/kv260_top.v" "$here/kv260.xdc" "$stage/"
ssh "$host" "rm -rf $rdir/build && mkdir -p $rdir/build"
rsync -a "$stage/" "$host:$rdir/build/"
ssh "$host" "cd $rdir/build && source $settings >/dev/null && \
  vivado -mode batch -nojournal -log vivado.log -source build.tcl -tclargs rtl/src out $clk_div >/dev/null && \
  printf 'all: { [destination_device = pl] out/protoemu.bit }\n' > bit.bif && \
  bootgen -image bit.bif -arch zynqmp -process_bitstream bin -w on >/dev/null && \
  grep PROTOEMU_RESULT vivado.log"

mkdir -p "$out"
rsync -a "$host:$rdir/build/out/protoemu.bit.bin" "$host:$rdir/build/out/"'*.rpt' "$host:$rdir/build/vivado.log" "$out/"
echo "built $out/protoemu.bit.bin (clk_div $clk_div)"
