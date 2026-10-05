#!/usr/bin/env bash
# Install the protoemu app on the Kria, load it, and copy the host software.
#
#   fpga/kv260/install.sh            # after build.sh; KRIA (default ubuntu@kria.[tailnet domain redacted])
#
# On the Kria: /lib/firmware/xilinx/protoemu/{protoemu.bit.bin,protoemu.dtbo,shell.json}
# (dtbo compiled there from protoemu.dtsi), `xmutil unloadapp; xmutil loadapp
# protoemu`, and ~/protoemu/{tools,firmware,fpga/kv260/host} for bringup_kria.py.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
kria=${KRIA:-ubuntu@kria.[tailnet domain redacted]}
test -f "$here/out/protoemu.bit.bin" || { echo "no out/protoemu.bit.bin: run build.sh first" >&2; exit 1; }
ssh "$kria" "mkdir -p protoemu-app protoemu/fpga/kv260"
rsync -a "$here/out/protoemu.bit.bin" "$here/protoemu.dtsi" "$here/shell.json" "$kria:protoemu-app/"
rsync -a --delete --exclude __pycache__ "$root/tools" "$root/firmware" "$kria:protoemu/"
rsync -a --delete --exclude __pycache__ "$here/host" "$kria:protoemu/fpga/kv260/"
ssh "$kria" 'set -e; cd protoemu-app
  dtc -q -@ -I dts -O dtb -o protoemu.dtbo protoemu.dtsi
  sudo mkdir -p /lib/firmware/xilinx/protoemu
  sudo cp protoemu.bit.bin protoemu.dtbo shell.json /lib/firmware/xilinx/protoemu/
  sudo xmutil unloadapp || true
  sudo xmutil loadapp protoemu
  sudo xmutil listapps | grep -E "protoemu|Accelerator"'
echo "loaded; test with: ssh $kria sudo python3 protoemu/fpga/kv260/host/bringup_kria.py"
