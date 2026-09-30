"""CRC tap vs reference USB CRCs (docs/isa.md §4.5), on the model."""
import random

from pe import asm, host, sim, usb
from pe.modelhost import ModelHost

# feed one bit per `in` from R0 (bit 0); the host writes R0 between steps
PROG = """
.program crcfeed
loop:
    wait 1 flag 0 clear
    in r0, 1
    flag set 1
    jmp loop
"""


def run_bits(bits, poly, width, init):
    mh = ModelHost()
    prog = asm.assemble(PROG)
    mh.apply(host.load_program(0, prog))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), poly | ((width - 1) << 16) | (1 << 20))
    mh.write(sim.core_reg(0, sim.C_CRC), init)
    mh.start(1)
    for b in bits:
        mh.write(sim.core_reg(0, sim.C_R0), b)
        mh.write(sim.A_FSET, 1)
        while not mh.chip.flags & 2:
            mh.step()
        mh.write(sim.A_FCLR, 2)
    return mh.chip.cores[0].crc


def lsb_bits(data):
    return [(byte >> i) & 1 for byte in data for i in range(8)]


def test_crc16_usb():
    rng = random.Random(7)
    for n in (0, 1, 8, 13):
        payload = bytes(rng.getrandbits(8) for _ in range(n))
        crc = run_bits(lsb_bits(payload), 0x8005, 16, 0xFFFF)
        field = usb.crc16(payload)
        # transmitted field = bit-reversed, inverted register
        sent = int.from_bytes(field, "little")
        assert int(f"{(~crc) & 0xFFFF:016b}"[::-1], 2) == sent
        # residue over payload + CRC field
        assert run_bits(lsb_bits(payload + field), 0x8005, 16, 0xFFFF) == 0x800D


def test_crc5_usb_token():
    for addr, endp in ((0, 0), (0x15, 0xE), (0x7F, 0xF), (3, 1)):
        tok = usb.token(usb.PID_IN, addr, endp)
        v = tok[1] | (tok[2] << 8)
        bits = [(v >> i) & 1 for i in range(16)]  # addr, endp, crc5 as sent
        assert run_bits(bits, 0x05, 5, 0x1F) == 0b01100
