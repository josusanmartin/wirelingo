#!/usr/bin/env bash
# Install the protoemu app on the Kria, load it, and copy the host software.
#
#   WL_KRIA=ubuntu@kria.example fpga/kv260/install.sh   # after build.sh
#   APP=protoemu48 fpga/kv260/install.sh   # a second app (e.g. the 48 MHz build), the first one kept
#   APP=protoemu3c LOAD=0 fpga/kv260/install.sh   # the 3-core build (build.sh --cores 3), not loaded
#
# WL_KRIA is the board's ssh address (a LAN IP or a tailnet name); the older
# name KRIA is still read when WL_KRIA is unset. With neither, it stops.
# OUT_DIR is build.sh's output (default fpga/kv260/out). The bitstream must be
# the build APP holds (host/fabric_guard.py APP_BUILD: core count and clk_div,
# from OUT_DIR/build_info.json), or it stops before copying anything.
# On the Kria: /lib/firmware/xilinx/$APP/{$APP.bit.bin,$APP.dtbo,shell.json}
# (dtbo compiled there from protoemu.dtsi, firmware-name = $APP.bit.bin),
# `xmutil unloadapp`, then `xmutil loadapp $APP`, then `xmutil listapps` (LOAD=0 skips it), and
# ~/$APP/{tools,firmware,fpga/kv260/host} for bringup_kria.py, with
# host/expected_build.json = this checkout's RTL revision (build_id.py info).
# APP defaults to protoemu.
set -euo pipefail
kria=${WL_KRIA:-${KRIA:-}}
[ -n "$kria" ] || { echo "set WL_KRIA to the board's ssh address, e.g. WL_KRIA=ubuntu@kria.example" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out=${OUT_DIR:-$here/out}
test -f "$out/protoemu.bit.bin" || { echo "no $out/protoemu.bit.bin: run build.sh first" >&2; exit 1; }
app=${APP:-protoemu}
case "$app" in *[!A-Za-z0-9_-]*|"") echo "APP must be a plain name" >&2; exit 1;; esac
# bringup_kria.py checks the loaded bitstream against this checkout's RTL revision
want=$(python3 "$here/build_id.py" info)
built=$(cat "$out/build_info.json" 2>/dev/null || echo '{}')
python3 "$here/host/app_build.py" "$app" "$want" "$built"
ssh "$kria" "mkdir -p $app-app $app/fpga/kv260"
rsync -a "$out/protoemu.bit.bin" "$kria:$app-app/$app.bit.bin"
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
    sudo xmutil listapps | grep -E \"^ *($app|Accelerator) |rror\""
  # xmutil can print "Load Error" and still exit 0, so this never says the load worked
  echo "load attempted. Run nothing unless the table above shows $app as its one active row and no line says error."
  echo "Then test with: ssh $kria sudo python3 $app/fpga/kv260/host/bringup_kria.py"
else
  echo "installed $app (not loaded). On the Kria, one command at a time, reading each:"
  echo "  sudo xmutil unloadapp"; echo "  sudo xmutil loadapp $app"; echo "  sudo xmutil listapps"
fi
