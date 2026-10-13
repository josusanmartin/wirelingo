#!/usr/bin/env bash
# Build the protoemu KV260 app on a remote Vivado host and fetch it.
#
#   fpga/kv260/build.sh [clk_div]        # 24 = 50 MHz (default), 25 = 48 MHz
#
# The RTL inputs must be committed (ALLOW_DIRTY=1 overrides): the bitstream
# records their revision (build_id.py, read back by host/bringup_kria.py).
# Env: VIVADO_THREADS (default 4), VIVADO_HOST (default runner1), REMOTE_DIR (default protoemu-flow/fpga,
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

# the bitstream carries its RTL revision (pe_lab, build_id.py): refuse a dirty one
dirty=$(python3 "$here/build_id.py" info | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["dirty"]))')
if [ "$dirty" = 1 ] && [ "${ALLOW_DIRTY:-0}" != 1 ]; then
  echo "RTL inputs have uncommitted changes (git status src flow/fpga_profile.py fpga/kv260);" >&2
  echo "commit them, or ALLOW_DIRTY=1 for a bitstream marked dirty" >&2
  exit 1
fi
# tapeout configuration: 2 cores, 8-deep FIFOs
python3 "$root/flow/fpga_profile.py" --output "$stage/rtl" --cores 2 --fifo-log2 3
cp "$here/build.tcl" "$here/kv260_top.v" "$here/pe_spim.v" "$here/pe_lab.v" "$here/kv260.xdc" "$stage/"
mkdir -p "$out"
python3 "$here/build_id.py" vh "$stage" "$stage/build_id.vh" "$stage/build_info.json"
ssh "$host" "rm -rf $rdir/build && mkdir -p $rdir/build"
rsync -a "$stage/" "$host:$rdir/build/"
ssh "$host" "cd $rdir/build && source $settings >/dev/null && \
  VIVADO_THREADS=${VIVADO_THREADS:-4} vivado -mode batch -nojournal -log vivado.log -source build.tcl -tclargs rtl/src out $clk_div >/dev/null && \
  printf 'all: { [destination_device = pl] out/protoemu.bit }\n' > bit.bif && \
  bootgen -image bit.bif -arch zynqmp -process_bitstream bin -w on >/dev/null && \
  grep PROTOEMU_RESULT vivado.log; grep \"CRITICAL WARNING\" vivado.log | sort | uniq -c || true"

rm -f "$out"/*.rpt "$out/protoemu.bit.bin"
cp "$stage/build_info.json" "$out/"
rsync -a "$host:$rdir/build/out/protoemu.bit.bin" "$host:$rdir/build/out/"'*.rpt' "$host:$rdir/build/vivado.log" "$out/"
echo "built $out/protoemu.bit.bin (clk_div $clk_div): $(cat "$out/build_info.json" | tr -d '\n ')"
