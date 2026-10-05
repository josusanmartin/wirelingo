"""Small instruction-level PIO test machine; not an RP2350 hardware model.

Decodes the pinned assembler's 16-bit instructions. Semantics follow the
RP2040 datasheet sections 3.4 and 3.5.4, including asynchronous autopull,
PULL's autopull barrier, counter saturation and side-set during stalls.
Only the instruction subset used by spi_stream is supported. DMA scheduling,
input synchronizer/pad delay and physical clocks are outside this model.
"""
from collections import deque
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def program_fixture():
    fixture = json.loads((ROOT / 'test/pio_program.json').read_text())
    source = ROOT / 'tools/micropython/pio_quad.py'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture['source_sha256'], \
        'PIO source changed: regenerate its fixture with test/compile_pio.py'
    return fixture


class PioMachine:
    def __init__(self):
        fixture = program_fixture()
        self.program = fixture['instructions']
        self.wrap_top = (fixture['execctrl'] >> 12) & 31
        self.wrap_bottom = (fixture['execctrl'] >> 7) & 31
        assert fixture['shiftctrl'] == 0x30000  # MSB first, auto push/pull 32.
        self.pc = self.wrap_bottom
        self.x = self.y = self.osr = self.isr = 0
        self.out_count, self.in_count = 32, 0
        self.tx, self.rx = deque(), deque()
        self.output, self.sck, self.delay = 0, 0, 0
        self.rx_limit = 4

    def _pull(self):
        self.osr = self.tx.popleft()
        self.out_count = 0

    def _push(self):
        self.rx.append(self.isr)
        self.isr, self.in_count = 0, 0

    def step(self, pins=0):
        if self.delay:
            self.delay -= 1
            if self.out_count >= 32 and self.tx:
                self._pull()
            return
        instruction = self.program[self.pc]
        self.sck = (instruction >> 12) & 1  # required one-bit side-set
        opcode, field, count = instruction >> 13, (instruction >> 5) & 7, instruction & 31
        successor = self.wrap_bottom if self.pc == self.wrap_top else self.pc + 1
        stalled = False
        if opcode == 0:  # JMP
            if field == 0:
                successor = count
            elif field == 2:
                if self.x:
                    successor = count
                self.x = (self.x - 1) & 0xFFFFFFFF
            elif field == 3:
                if not self.y:
                    successor = count
            else:
                raise AssertionError('unsupported JMP condition')
        elif opcode == 2:  # IN
            count = count or 32
            assert field in (0, 3)
            # A blocked automatic push preserves the sampled value; waiting
            # before shifting here is equivalent while the SPI pins are held.
            if self.in_count + count >= 32 and len(self.rx) == self.rx_limit:
                stalled = True
            else:
                value = pins if field == 0 else 0
                self.isr = ((self.isr << count) | (value & ((1 << count) - 1))) & 0xFFFFFFFF
                self.in_count = min(32, self.in_count + count)
                if self.in_count >= 32:
                    self._push()
        elif opcode == 3:  # OUT
            count = count or 32
            if self.out_count >= 32:
                if self.tx:
                    self._pull()
                stalled = True  # empty OSR cannot fill and shift in one cycle
            else:
                value = self.osr >> (32 - count)
                self.osr = (self.osr << count) & 0xFFFFFFFF
                self.out_count = min(32, self.out_count + count)
                if field == 0:
                    self.output = value & 15
                elif field == 1:
                    self.x = value
                elif field == 2:
                    self.y = value
                else:
                    assert field == 3  # NULL padding discard
                if self.out_count >= 32 and self.tx:
                    self._pull()
        elif opcode == 4:  # blocking PUSH/PULL
            if instruction & 0x80:
                if self.out_count < 32:
                    pass  # autopull turns PULL into a barrier, not overwrite
                elif self.tx:
                    self._pull()
                else:
                    stalled = True
            elif len(self.rx) == self.rx_limit:
                stalled = True
            else:
                self._push()
        elif opcode == 5:  # MOV (including NOP = MOV Y,Y)
            source = count & 7
            assert source in (2, 3)
            value = self.y if source == 2 else 0
            if count & 8:
                value ^= 0xFFFFFFFF
            if field == 2:
                self.y = value
            else:
                assert field == 6
                self.isr, self.in_count = value, 0
        else:
            raise AssertionError('unsupported PIO opcode')
        if opcode != 3 and self.out_count >= 32 and self.tx:
            self._pull()
        if not stalled:
            self.pc, self.delay = successor, (instruction >> 8) & 15
