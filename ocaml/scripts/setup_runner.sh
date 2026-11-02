#!/usr/bin/env bash
# Toolchain for ocaml/ on a host without opam: opam 2.6.1 in ~/.local/bin, a
# switch "wl" (OCaml 5.3.0, dune, hardcaml v0.17.1, ppx_hardcaml), all in the
# home directory, no sudo.
#
#   ocaml/scripts/setup_runner.sh SYSROOT     SYSROOT: a directory for the gmp headers
#
# hardcaml needs zarith, zarith needs gmp's headers, and runner1 has libgmp10 but
# not libgmp-dev. So the -dev package is unpacked under SYSROOT, its libgmp.so
# pointed at the system library. Afterwards, in any shell:
#
#   export OPAMROOT=$HOME/.opam PATH=$HOME/.local/bin:$PATH \
#          LIBRARY_PATH=SYSROOT/usr/lib/x86_64-linux-gnu
#   eval "$(opam env --switch=wl --set-switch)"
set -euo pipefail
sys=$(realpath -m "$1")
mkdir -p ~/.local/bin "$sys"
export PATH=$HOME/.local/bin:$PATH OPAMROOT=$HOME/.opam OPAMYES=1 OPAMCONFIRMLEVEL=unsafe-yes
[ -x ~/.local/bin/opam ] || {
  curl -fsSL -o ~/.local/bin/opam \
    https://github.com/ocaml/opam/releases/download/2.6.1/opam-2.6.1-x86_64-linux
  chmod +x ~/.local/bin/opam
}
[ -d "$OPAMROOT" ] || opam init --bare --disable-sandboxing --no-setup -y https://opam.ocaml.org
tmp=$(mktemp -d)
(cd "$tmp" && apt-get download libgmp-dev && dpkg -x libgmp-dev*.deb "$sys")
rm -rf "$tmp"
lib=$sys/usr/lib/x86_64-linux-gnu
ln -sf /usr/lib/x86_64-linux-gnu/libgmp.so.10 "$lib/libgmp.so"
rm -f "$lib"/libgmp.a "$lib"/libgmpxx*
export C_INCLUDE_PATH=$sys/usr/include:$sys/usr/include/x86_64-linux-gnu CPATH=$sys/usr/include:$sys/usr/include/x86_64-linux-gnu LIBRARY_PATH=$lib
opam switch list --short | grep -qx wl || opam switch create wl ocaml-base-compiler.5.3.0 --no-install -y
opam install --switch=wl -y --assume-depexts dune hardcaml ppx_hardcaml
opam clean -a -c -s --logs
