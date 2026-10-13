#!/usr/bin/env bash
# Install the protoemu app on the Kria, load it, and copy the host software.
#
#   fpga/kv260/install.sh            # after build.sh; KRIA (default ubuntu@kria.[tailnet domain redacted])
#
# On the Kria: /lib/firmware/xilinx/protoemu/{protoemu.bit.bin,protoemu.dtbo,shell.json}
# (dtbo compiled there from protoemu.dtsi), `xmutil unloadapp; xmutil loadapp
# protoemu`, and ~/protoemu/{tools,firmware,fpga/kv260/host} for bringup_kria.py,
# with host/expected_build.json = this checkout's RTL revision (build_id.py info).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
kria=${KRIA:-ubuntu@kria.[tailnet domain redacted]}
test -f "$here/out/protoemu.bit.bin" || { echo "no out/protoemu.bit.bin: run build.sh first" >&2; exit 1; }
# bringup_kria.py checks the loaded bitstream against this checkout's RTL revision
want=$(python3 "$here/build_id.py" info)
built=$(cat "$here/out/build_info.json" 2>/dev/null || echo '{}')
python3 - "$want" "$built" <<'PY'
import json, sys
want, built = json.loads(sys.argv[1]), json.loads(sys.argv[2])
print("checkout RTL revision", want["rtl_rev"][:12], "(dirty)" if want["dirty"] else "")
print("bitstream RTL revision", built.get("rtl_rev", "unknown")[:12], "(dirty)" if built.get("dirty") else "")
if built.get("rtl_rev") != want["rtl_rev"] or want["dirty"]:
    print("WARNING: out/protoemu.bit.bin was not built from this checkout's RTL; bringup_kria.py will fail its build step")
PY
ssh "$kria" "mkdir -p protoemu-app protoemu/fpga/kv260"
rsync -a "$here/out/protoemu.bit.bin" "$here/protoemu.dtsi" "$here/shell.json" "$kria:protoemu-app/"
rsync -a --delete --exclude __pycache__ "$root/tools" "$root/firmware" "$kria:protoemu/"
rsync -a --delete --exclude __pycache__ "$here/host" "$kria:protoemu/fpga/kv260/"
printf '%s\n' "$want" | ssh "$kria" "cat > protoemu/fpga/kv260/host/expected_build.json"
ssh "$kria" 'set -e; cd protoemu-app
  dtc -q -@ -I dts -O dtb -o protoemu.dtbo protoemu.dtsi
  sudo mkdir -p /lib/firmware/xilinx/protoemu
  sudo cp protoemu.bit.bin protoemu.dtbo shell.json /lib/firmware/xilinx/protoemu/
  sudo xmutil unloadapp || true
  sudo xmutil loadapp protoemu
  sudo xmutil listapps | grep -E "protoemu|Accelerator"'
echo "loaded; test with: ssh $kria sudo python3 protoemu/fpga/kv260/host/bringup_kria.py"
