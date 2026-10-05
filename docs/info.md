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

Programs live in on-chip SRAM: each core has its own bank of 256 instructions.

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
* SCK up to the project clock (about 50 Mbit/s); an optional quad mode moves
  4 bits per SCK (about 200 Mbit/s) on ui[5:2] / uo[7:4] (docs/isa.md §6.1).
  Reads have 8 dummy SCK (one byte) after the address.
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

`protoemu_fw.py` holds assembled programs (UART, SPI, I2C, WS2812, USB, 10BASE-T TX)
ready for `pe.load(**PROGRAM)`. Assemble your own with `tools/pe/asm.py`.
Clock per demo: 48 MHz for UART/I2C/SPI/USB, 40 MHz for 10BASE-T
(`bringup.setup(40_000_000)`, then `pe.eth10_send(core, frame)`).
The optional [PIO/DMA transport](board-transport.md) keeps the same driver
interface. It requires explicit state-machine/GPIO ownership and a compatible
PIO GPIO window; its board throughput and sampling margin are unmeasured.

## External hardware

None for the loopback and UART demos. For the others (3.3 V I/O; values are
derived from the specifications, not yet validated on silicon):

* **USB low-speed device:** D+ on `uio[0]`, D- on `uio[1]`, each through 27 Ω
  to the connector; 1.5 kΩ from D- to 3.3 V (marks a low-speed device);
  48 MHz project clock. Copy the generated `usb_device.py`/`usb_bits.py`,
  `protoemu.py`, `protoemu_fw.py` and `bringup.py` to the RP2350; call
  `sw = bringup.usb_setup(pe)` and repeatedly call `sw.service()`. This example
  advertises an endpoint-zero vendor interface, with no HID/report endpoint or
  string descriptors. It is digitally tested; board enumeration remains a
  separate hardware check. See `docs/review-fixes.md` for the record ABI.
* **10BASE-T TX:** TD+ on `uo[0]`, TD- on `uo[1]`, into an RJ45 jack with 1:1
  magnetics. 802.3 asks for 2.2–2.8 V peak differential into 100 Ω. The two
  nominal pins swing ±3.3 V differentially. The proposed 15–20 Ω series
  resistors per leg are an unmeasured starting point: actual pad resistance,
  magnetics and waveform need characterization. The documented TX mode uses
  a 40 MHz project clock; no electrical compliance result is established.
* **10BASE-T RX:** the digital test drives a binary comparator output on `ui[0]`
  (P16): high for positive receive differential voltage, low for negative,
  idle low. The input must be 0..3.3 V with no negative excursion, and have
  enough edge fidelity for the ±13.5 ns digital jitter test. An implementation
  needs receive magnetics and a terminated differential comparator stage;
  the comparator threshold, idle squelch, hysteresis, propagation delay and
  supply protection have not been measured. The
  [receive bench circuit](eth10-receiver.md) now specifies magnetics, component
  values, package pins and a finite FCS-checked capture service. Its schematic
  is proposed and unbuilt; the passing receive tests use a binary digital input.
  External RX tests use 50 MHz and the original TX mode uses 40 MHz. A digital
  internal TX→RX loopback now passes at 50 MHz with TX divider 1.25 and RX
  period 5. Its TX half cells are 40/60 ns; that mode has no established
  electrical compliance or simultaneous board result.
* **I2C:** SDA `uio[0]`, SCL `uio[1]`, open drain (set `OD_MASK`), 4.7 kΩ
  pull-ups to 3.3 V.
* **CAN:** a 3.3 V transceiver (for example SN65HVD230); TXD from an output
  pin, RXD to an input pin.
* **SWD / JTAG:** direct to the target's debug header at 3.3 V.
