#!/usr/bin/env python3
"""PMOD J2 jumper loopback self-test (needs four jumpers: J2 pin 1-7, 2-8,
3-9, 4-10; see fpga/kv260/README.md). Not run by the other scripts.

    sudo python3 fpga/kv260/host/pmod_test.py

1. Each top-row pin in turn is driven from protoemu (uo0, PIN_OUT), with the
   other seven pins as inputs; PMODIN must show the level on exactly its
   jumpered partner. This also confirms the header-pin mapping.
2. UART at 115200 through the wire: core 0 TX on uo0 -> J2 pin 1 -> pin 7 ->
   ui0 -> core 1 RX.
3. uio2 open drain on pin 2 -> pin 8 -> uio3 (an input): PINS[3] follows.
All PMOD pins are disabled again at the end.
"""
import sys
import time

from pe_kria import ROOT, Board
from pe_spim import Spim
from pe import asm, host, sim  # noqa: E402

UO0, UI0 = 8, 16


def main():
    board, spim = Board(), Spim()
    spim.select(fabric=False)
    board.reset()
    pe = board.protoemu()
    ok = True
    try:
        # 1. pairs
        for top in range(4):
            spim.pmod({top: UO0})
            seen = []
            for level in (0, 1):
                pe.write(sim.A_POUT, level << 8)
                time.sleep(0.001)
                seen.append(spim.pmod_in())
            partner = [p for p in range(8) if p != top and (seen[0] >> p & 1) == 0 and (seen[1] >> p & 1) == 1]
            print(f"  J2 pin {top + 1} driven: follows on pmod{partner} (expected [{top + 4}], J2 pin {top + 7}); "
                  f"PMODIN low 0x{seen[0]:02X} high 0x{seen[1]:02X}")
            ok &= partner == [top + 4]
        pe.write(sim.A_POUT, 0)
        # 2. UART through the jumper
        spim.pmod({0: UO0, 4: UI0})
        pe.stop(3)
        pe.clear_fifos(3)
        div = host.clkdiv_for(board.clk_hz(), 8 * 115200)
        for core, name, d, cfg in ((1, "uart_rx", {"RX": UI0}, dict(in_pins=UI0)),
                                   (0, "uart_tx", {}, dict(out_pins=(UO0, 1), side_pins=UO0))):
            prog = asm.assemble((ROOT / "firmware" / f"{name}.pasm").read_text(), d)
            for a, w in host.coalesce(host.load_program(core, prog, core * 256, clkdiv=div, **cfg)):
                pe.write(a, w)
        pe.write(sim.A_POUT, 1 << UO0)                    # idle high before RX starts
        pe.start(2)
        pe.start(1)
        msg = b"PMOD ok"
        for ch in msg:
            pe.push(0, ch)
        time.sleep(0.01)
        got = bytes(w & 0xFF for w in pe.pop_all(1))
        pe.stop(3)
        print(f"  UART 115200 J2 pin 1 -> pin 7: sent {msg!r} got {got!r}")
        ok &= got == msg
        # 3. open drain uio2 -> uio3
        spim.pmod({1: 2, 5: 3})
        pe.write(sim.A_OD, 1 << 2)
        pe.write(sim.A_POE, 0)
        levels = []
        for v in (0, 1, 0):
            pe.write(sim.A_POUT, v << 2)
            time.sleep(0.001)
            levels.append((pe.read(sim.A_PINS) >> 3) & 1)
        print(f"  uio2 open drain 0/release/0 on J2 pin 2 -> uio3 on pin 8 reads {levels} "
              f"(released level needs a pull-up; FPGA pins have none here)")
        ok &= levels[0] == 0 and levels[2] == 0
    finally:
        spim.pmod({})
        pe.write(sim.A_OD, 0)
        pe.write(sim.A_POUT, 0)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
