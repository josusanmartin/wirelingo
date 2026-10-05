#!/bin/bash
# User-space sigrok-cli (no root): downloads the Ubuntu/Debian packages of
# sigrok-cli, libsigrok, libsigrokdecode (with its protocol decoders) and the
# libraries they need that a desktop install may lack, unpacks them under
# DEST (default ~/tools/sigrok) and writes DEST/sigrok-cli, a wrapper that
# sets LD_LIBRARY_PATH and SIGROKDECODE_DIR. tools/pe/sigrok.py finds it
# there; or `sudo apt install sigrok-cli` and skip this.
#   flow/install_sigrok.sh [DEST]
# Tested on Ubuntu 22.04 (sigrok-cli 0.7.2, libsigrokdecode 0.5.3, which
# embeds the system's libpython3.10).
set -euo pipefail
DEST=$(realpath -m "${1:-$HOME/tools/sigrok}")
mkdir -p "$DEST/debs" "$DEST/root"
cd "$DEST/debs"
apt-get download sigrok-cli libsigrok4 libsigrokdecode4 libftdi1-2 libhidapi-hidraw0 \
  libieee1284-3 libserialport0 libzip4 libbluetooth3
for d in *.deb; do dpkg -x "$d" "$DEST/root"; done
cat > "$DEST/sigrok-cli" <<'WRAP'
#!/bin/sh
# user-space sigrok-cli (flow/install_sigrok.sh in protoemu)
R=$(dirname "$(readlink -f "$0")")/root
export LD_LIBRARY_PATH="$R/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export SIGROKDECODE_DIR="$R/usr/share/libsigrokdecode/decoders"
unset PYTHONHOME PYTHONPATH
exec "$R/usr/bin/sigrok-cli" "$@"
WRAP
chmod +x "$DEST/sigrok-cli"
"$DEST/sigrok-cli" --version | head -1
"$DEST/sigrok-cli" -L | grep -cE '^  (uart|spi|i2c|usb_packet|can) ' | xargs echo "decoders found (of 5 needed):"
