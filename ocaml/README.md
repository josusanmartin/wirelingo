# Wirelingo in OCaml

A second implementation of Wirelingo in OCaml: the RTL in
[Hardcaml](https://github.com/janestreet/hardcaml), and the assembler, cycle model and
host tools. The chip stays the Verilog in `src/`. Nothing here replaces it; this port has
to be shown equal to it, and [docs/hardcaml-port.md](../docs/hardcaml-port.md) says what is
proved, how, and what is not done.

| directory | what |
|---|---|
| `rtl/` | every module of `src/` in Hardcaml (`Signal`, `Always`, `Interface`, `Hierarchy`) |
| `bin/gen_rtl` | writes the Verilog; `tapeout` is the frozen configuration |
| `formal/` | proof of the generated Verilog against `src/` (yosys, ABC) |
| `asm/` | the assembler (`tools/pe/asm.py`), `isa.py`, `host.py`, `profile.py` |
| `sim/` | the cycle model (`sim.py`, `line.py`) and `modelhost.py` |
| `bin/wl_asm`, `wl_export`, `wl_run` | assemble; export for MicroPython; run on the model |
| `bin/wl_simcheck` | the OCaml model driven by a stimulus stream, compared every cycle |
| `test/` | parity and lockstep harnesses against the Python tools and the RTL |
| `wave_test/` | Hardcaml waveform expect tests with assertions (`dune test`) |
| `scripts/` | toolchain setup, cocotb runner, `evidence.sh` (all gates) |

## Build

Tested with OCaml 5.3.0, dune 3.24.2, hardcaml v0.17.1. `scripts/setup_runner.sh SYSROOT`
installs them under the home directory, without sudo. Then:

```
export OPAMROOT=$HOME/.opam PATH=$HOME/.local/bin:$PATH \
       LIBRARY_PATH=SYSROOT/usr/lib/x86_64-linux-gnu
eval "$(opam env --switch=wl --set-switch)"
cd ocaml && dune build && dune test
```

`LIBRARY_PATH` is only needed where libgmp-dev is missing, as on runner1.

## Use

```
_build/default/bin/gen_rtl.exe tapeout OUT          # OUT/wirelingo_hc.v and friends
_build/default/bin/wl_asm.exe ../firmware/uart_rx.pasm -D RX=16
_build/default/bin/wl_export.exe ../firmware/uart_tx.pasm --define TX=8 --out-pins 8,1
_build/default/bin/wl_run.exe ../firmware/uart_tx.pasm --define TX=8 --out-pins 8,1 \
    --clkdiv 2 --push 0x55 --cycles 4000            # source to pin changes, OCaml only
```

## Check

Run these on runner1 (heavy compute does not belong on the shared box):

```
formal/equiv_port.sh -j 6 --mutants OUT      # proof against src/
scripts/run_cocotb.sh WORK test lockstep     # the repo's cocotb suites on the generated Verilog
scripts/stage3_model_suite.sh OUT            # model suite, every Chip in lockstep with OCaml
python3 test/asm_parity.py                   # assembler against asm.py
scripts/evidence.sh OUT                      # everything, with floors on the counts
```

`firmware/swd_probe.pasm` and SWD tools are excluded everywhere.
