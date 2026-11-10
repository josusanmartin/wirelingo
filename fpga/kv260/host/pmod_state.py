#!/usr/bin/env python3
"""Read pe_spim's PMOD0/PMOD1 enables and PMODIN (read-only; nothing is written):

    sudo python3 fpga/kv260/host/pmod_state.py

Exit 0 when every PMOD pin is disabled (PMOD0 = PMOD1 = 0), 1 otherwise. The
board smoke script (fpga/kv260/board_smoke.py) runs it before and after its
tests, none of which enables a pin. Opens the fabric only through
fabric_guard (pe_spim.SpimMem), like every other board tool.
"""
import sys

from pe_spim import PMOD0, PMOD1, PMODIN, VERSION, VERSION_VALUE


def state(mem):
    """(ok, line) from a SpimMem-like object; reads only."""
    if mem.rd(VERSION) != VERSION_VALUE:
        return False, f"pe_spim not found (VERSION 0x{mem.rd(VERSION):08X})"
    p0, p1, pin = mem.rd(PMOD0), mem.rd(PMOD1), mem.rd(PMODIN) & 0xFF
    return p0 == 0 and p1 == 0, f"PMOD0 {p0:08x} PMOD1 {p1:08x} PMODIN {pin:02x}"


def main(mem=None):
    if mem is None:
        from pe_spim import SpimMem
        mem = SpimMem()
    ok, line = state(mem)
    print(line + ("" if ok else ": a PMOD pin is enabled"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
