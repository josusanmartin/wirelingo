<!---
This file is used to generate the project datasheet.
-->

## How it works

Wirelingo (repository name protoemu) is a programmable protocol emulator:
small processor cores built for
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
* CALL/RET with one link register, a WAIT until masked pins differ from a
  register, and a per-pin bypass of the input synchroniser.
* A line unit on core 0 for NRZ, NRZI and Manchester framing with bit
  stuffing, sampled mid-bit on a half-cycle phase so that full-speed USB
  tolerates the specification's edge jitter.

The firmware verified in simulation covers UART TX/RX, SPI master, I2C master
(with clock stretching), USB devices at low and full speed (48 MHz), including
one that enumerates on its own with no host traffic after the load, a
10BASE-T transmitter (at 40 MHz, with link pulses) and receiver, CAN 2.0A
TX/RX, an SWD debug probe, a JTAG engine, 1-Wire, PS/2, WS2812, an I2C sniffer,
DMX512, LIN, NEC infrared, I2S, Wiegand and DShot600 (also bidirectional).
None of it has run on silicon yet.

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

SPI master and UART receive helpers use core 0 and the `uio` pins (the driver
writes all of `ui_in`): SCK `uio[2]`, MOSI `uio[3]`, MISO `uio[4]`, CS `uio[5]`
(add a pull-up: CS floats until the first SPI call), UART RX `uio[6]`.

```
rx = bringup.spi_transfer(pe, b"\x9f\0\0\0", mode=3)  # modes 0-3, CS held low by the host
bringup.spi_packed_setup(pe, mode=0)                   # firmware CS: exact 8N-bit frames
rx = bringup.spi_packed_transfer(pe, [b"\x06", b"\x02\0\x10" + data])  # queued, 1-8192 B each
bringup.spi_preloaded_setup(pe)                        # mode 0, 1-32 B preloaded, halts after
rx = bringup.spi_preloaded_transfer(pe, bytes(range(32)))
data, framing_error = bringup.uart_capture(pe, 16)     # 8N1: 1-32 B, or a multiple of 4 above
```

SCK is 1 MHz by default (`div=12`: 4 ticks of 12 clocks per bit). The
packed/preloaded framing and the capture's framing-error rule are those of
`docs/spi-packed-host.md` and `docs/uart-capture-host.md`. These helpers pass
model tests (`test/model/test_micropython_spi_uart.py`); none has run on a
demo board yet.

`protoemu_fw.py` holds assembled programs (UART, UART capture, SPI in modes 0-3,
packed and preloaded SPI, I2C, WS2812, USB, 10BASE-T TX)
ready for `pe.load(**PROGRAM)`, the autonomous USB device for
`bringup.usb_enum(pe, "low")` or `"full"`, and the UART-to-USB pairs for
`bringup.usb_uart(pe, "overlap4")`, `"envelope3"` or `"byte"` (UART in on
`uio[6]`, reports read by a PC; wiring below). Assemble your own with `tools/pe/asm.py`.
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
  separate hardware check. Each received DATA packet arrives as a record:
  payload words, then a trailer with the bit count in [31:16], the reversed
  PID in [15:8], [7] = 1, SETUP association in [1] and CRC/PID good in [0]
  (`firmware/usb_ls_rx.pasm`).
* **Autonomous USB device (low or full speed):** 48 MHz project clock, 27 Ω in
  series with each line. Low speed: D+ on `uio[0]`, D- on `uio[1]`, 1.5 kΩ from
  D- to 3.3 V. Full speed: D- on `uio[0]`, D+ on `uio[1]`, 1.5 kΩ from D+ to
  3.3 V. Call `bringup.usb_enum(pe, speed)`, then connect the cable; the chip
  answers the PC's enumeration by itself (vendor ID 0x1209, product 0x5634,
  one configuration, no data endpoint), and `bringup.usb_enum_state(pe)` reads
  the address and Configured state afterwards. Tested in RTL and
  timing-annotated gate-level simulation and in simulation of the KV260 FPGA
  build; it has not yet enumerated on a real PC, and full-speed signalling
  through the chip's pads is unmeasured.
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
* **UART over USB to a PC (overlap4, envelope3 and the one-byte mailbox):**
  48 MHz project clock. USB as for the low-speed device above: D+ on `uio[0]`,
  D- on `uio[1]`, 27 Ω in series with each, 1.5 kΩ from D- to 3.3 V. The UART
  sender's TX goes to `uio[6]` (3.3 V, 115200 8N1; a 10 kΩ pull-up to 3.3 V
  keeps it idle high when the sender is unplugged) and its flow-control input
  to READY on `uo[0]`, active high. The sender may start one frame per fresh
  READY rise, only while READY is high, and must finish each frame it starts;
  a USB-UART adapter's active-low, byte-granular CTS is not this contract, so
  the sender is typically a small microcontroller. ERROR on `uo[1]` (overlap4,
  envelope3) goes high, sticky, after a bad STOP bit. On the RP2350 call
  `bringup.usb_uart(pe, "overlap4")` (or `"envelope3"`, `"byte"`): it loads both
  cores and starts them, and the chip needs no host-port traffic afterwards.
  On the PC, `PYTHONPATH=tools python3 -m pe.usb_uart_reports overlap4` (or
  `envelope3`, `byte`) polls the vendor request and prints the UART bytes; it
  needs pyusb, the decoders do not. The pairs' own default RX is `ui[0]`, which
  the board driver holds low, so the board images move RX to `uio[6]`; that
  changes only the receiving core's words and the pin owners. Tested on the
  cycle model only (`test/model/test_micropython_usb_uart.py`: the board code
  loads the pair, a pins-only sender drives `uio[6]`, the USB host model
  enumerates and the PC reader reads the reports). It has not run on a demo
  board or a PC; the RTL tests ran the default `ui[0]` images.
* **I2C:** SDA `uio[0]`, SCL `uio[1]`, open drain (set `OD_MASK`), 4.7 kΩ
  pull-ups to 3.3 V.
* **CAN:** a 3.3 V transceiver (for example SN65HVD230); TXD from an output
  pin, RXD to an input pin.
* **SWD / JTAG:** direct to the target's debug header at 3.3 V.
