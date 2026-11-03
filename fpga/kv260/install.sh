#!/usr/bin/env bash
# Install the protoemu app on the Kria, load it, and copy the host software.
#
#   fpga/kv260/install.sh            # after build.sh; KRIA (default ubuntu@kria.[tailnet domain redacted])
#   APP=protoemu48 fpga/kv260/install.sh   # a second app (e.g. the 48 MHz build), the first one kept
#
# On the Kria: /lib/firmware/xilinx/$APP/{$APP.bit.bin,$APP.dtbo,shell.json}
# (dtbo compiled there from protoemu.dtsi, firmware-name = $APP.bit.bin),
# `xmutil unloadapp`, then `xmutil loadapp $APP`, then `xmutil listapps` (LOAD=0 skips it), and
# ~/$APP/{tools,firmware,fpga/kv260/host} for bringup_kria.py, with
# host/expected_build.json = this checkout's RTL revision (build_id.py info).
# APP defaults to protoemu.
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
app=${APP:-protoemu}
case "$app" in *[!A-Za-z0-9_-]*|"") echo "APP must be a plain name" >&2; exit 1;; esac
ssh "$kria" "mkdir -p $app-app $app/fpga/kv260"
rsync -a "$here/out/protoemu.bit.bin" "$kria:$app-app/$app.bit.bin"
sed "s/firmware-name = \"protoemu.bit.bin\"/firmware-name = \"$app.bit.bin\"/" "$here/protoemu.dtsi" | ssh "$kria" "cat > $app-app/$app.dtsi"
rsync -a "$here/shell.json" "$kria:$app-app/"
rsync -a --delete --exclude __pycache__ "$root/tools" "$root/firmware" "$kria:$app/"
rsync -a --delete --exclude __pycache__ "$here/host" "$kria:$app/fpga/kv260/"
printf '%s\n' "$want" | ssh "$kria" "cat > $app/fpga/kv260/host/expected_build.json"
ssh "$kria" "set -e; cd $app-app
  grep -q 'firmware-name = \"$app.bit.bin\"' $app.dtsi
  dtc -q -@ -I dts -O dtb -o $app.dtbo $app.dtsi
  sudo mkdir -p /lib/firmware/xilinx/$app
  sudo cp $app.bit.bin $app.dtbo shell.json /lib/firmware/xilinx/$app/"
if [ "${LOAD:-1}" = 1 ]; then
  ssh "$kria" "set -e; sudo xmutil unloadapp || true
    sudo xmutil loadapp $app
    sudo xmutil listapps | grep -E \"$app|Accelerator\""
  # xmutil can print "Load Error" and still exit 0, so this never says the load worked
  echo "load attempted. Run nothing unless the table above shows $app as its one active row and no line says error."
  echo "Then test with: ssh $kria sudo python3 $app/fpga/kv260/host/bringup_kria.py"
else
  echo "installed $app (not loaded). On the Kria, one command at a time, reading each:"
  echo "  sudo xmutil unloadapp"; echo "  sudo xmutil loadapp $app"; echo "  sudo xmutil listapps"
fi
