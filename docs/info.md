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

The host talks to the chip over SPI mode 0:

* `ui[7]` = CS_n, `ui[6]` = SCK, `ui[5]` = MOSI, `uo[7]` = MISO.
* Timing: SCK period ≥ 8 project clocks, high and low ≥ 3 each.
* Transaction: `cmd addr[15:0] data…`. The commands are 0x02 write, 0x03 read,
  0x0A write-fixed and 0x0B read-fixed. Data is 32-bit big-endian words.
* Reading address 0x0800 returns the ID `0x50450002`.

On the demo board, copy `tools/micropython/protoemu.py`, `protoemu_fw.py` and
`bringup.py` to the RP2350, then:

```
import bringup
pe = bringup.setup()            # select the project, 48 MHz clock, reset, check the ID
bringup.uart_loopback(pe)       # no wiring: core 0 UART TX -> internal pin -> core 1 UART RX
bringup.uart_tx(pe, b"hi\r\n")  # 115200 8N1 on uo[0]
```

`protoemu_fw.py` holds assembled programs (UART, SPI, I2C, WS2812, 10BASE-T TX)
ready for `pe.load(**PROGRAM)`. Assemble your own with `tools/pe/asm.py`.
Clock per demo: 48 MHz for UART/I2C/SPI/USB, 40 MHz for 10BASE-T
(`bringup.setup(40_000_000)`, then `pe.eth10_send(core, frame)`).

## External hardware

None for the loopback and UART demos. For the others (3.3 V I/O; values are
derived from the specifications, not yet validated on silicon):

* **USB low-speed device:** D+ on `uio[0]`, D- on `uio[1]`, each through 27 Ω
  to the connector; 1.5 kΩ from D- to 3.3 V (marks a low-speed device);
  48 MHz project clock. The host software is in `test/model/test_usb_enum.py`
  (`DeviceSoftware`).
* **10BASE-T TX:** TD+ on `uo[0]`, TD- on `uo[1]`, into an RJ45 jack with 1:1
  magnetics. 802.3 asks for 2.2–2.8 V peak differential into 100 Ω. The two
  pins swing ±3.3 V differentially, so use about 15–20 Ω in series per leg
  (counting the pad's own output resistance), and AC-couple if the magnetics'
  centre tap is not floating. 40 MHz project clock.
* **I2C:** SDA `uio[0]`, SCL `uio[1]`, open drain (set `OD_MASK`), 4.7 kΩ
  pull-ups to 3.3 V.
* **CAN:** a 3.3 V transceiver (for example SN65HVD230); TXD from an output
  pin, RXD to an input pin.
* **SWD / JTAG:** direct to the target's debug header at 3.3 V.
