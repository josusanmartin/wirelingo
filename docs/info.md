<!---
This file is used to generate the project datasheet.
-->

## How it works

protoemu is a programmable protocol emulator: small processor cores built for
bit-banging, which implement serial protocols in firmware instead of fixed
logic. Each core executes one 32-bit instruction per tick and has:

* a fractional clock divider;
* registers R0–R3 with a 16-bit ALU;
* input and output shift registers with auto-push/pull;
* a configurable CRC tap fed by the shift registers;
* 8-word FIFOs to the host, which can be joined into one 16-word TX FIFO.

This tapeout has two cores.

Programs live in on-chip SRAM, with 256 instructions per bank, and banks can be
reassigned between cores.

The instruction set is inspired by the RP2040's PIO, with additions that make
real protocols fit:

* WAIT with a timeout, on levels or on rising/falling/any edges (tracked every
  clock cycle), optionally qualified by a second pin. For example,
  `wait 0 pin DP if pin DM == 0` detects a USB end-of-packet.
* An ALU with flags, 16-bit immediates, and branches on registers, pins, flags
  and FIFO state.
* Per-pin open drain, direct single-pin control on any pin, virtual pins and
  global flags for coordinating cores.

The firmware verified in simulation covers UART TX/RX, SPI master, I2C master
(with clock stretching), a low-speed USB device (up to full enumeration, with
the CRC16 checked by the CRC tap), a 10BASE-T transmitter (at 40 MHz, with
link pulses), CAN 2.0A TX/RX, an SWD debug probe, a JTAG engine, 1-Wire,
PS/2, WS2812 and an I2C sniffer.

The full specification is in `docs/isa.md` in the repository.

## How to test

The host talks to the chip over SPI mode 0 (SCK ≤ clk/8):

* `ui[7]` = CS_n, `ui[6]` = SCK, `ui[5]` = MOSI, `uo[7]` = MISO.
* Transaction: `cmd addr[15:0] data…` with commands 0x02 write, 0x03 read,
  0x0A write-fixed and 0x0B read-fixed. Data is 32-bit big-endian words.
* Reading address 0x0800 returns the ID `0x50450002`.

On the demo board, `tools/micropython/protoemu.py` provides the driver.
Assemble a program on a PC with `tools/pe/asm.py`, export it with
`python -m pe.export`, load it into a core, start the core and exchange data
through its FIFO. For example, a UART transmitter on `uo[0]`:

```
pe.load(core=0, base=0, words=[...], clkdiv=..., pinctrl0=..., pinctrl1=..., shiftctrl=..., wrap=...)
pe.start(1)
pe.push(0, ord('A'))
```

## External hardware

None is required for the loopback and UART demos. Protocol demos connect to
the respective devices, for example a USB low-speed connector (D+ on `uio[0]`,
D- on `uio[1]` with a 1.5 kΩ pull-up on D-, 48 MHz clock) or an SWD target.
10BASE-T TX drives TD+/TD- (`uo[0]`/`uo[1]`) through the usual series
resistors into an RJ45 jack with magnetics, with a 40 MHz clock. CAN needs a
transceiver.
