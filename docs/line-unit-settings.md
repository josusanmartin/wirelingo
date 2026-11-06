# Line unit settings: which protocol uses what

The line unit ([isa.md §4.7](isa.md#47-line-unit-core-0)) is a configurable
line coder beside core 0. It is fixed hardware that offers a menu: NRZ, NRZI
or Manchester coding, bit stuffing after K equal bits or K ones, a choice of
frame endings (release, SE0, high, hold), and receive clock recovery. The menu
was built with USB and 10BASE-T in mind, and CAN-style stuffing also fits.
Software picks from it but cannot add a coding. The choice is three
write-only registers, LINECFG, LINEDIV and LINEPINS
([isa.md §6.2](isa.md#62-register-map-32-bit-word-addresses)), which the host
writes over SPI when it loads a program, like the core's clock divider and pin
settings. The core then feeds and drains the unit with `OUT LINE` and
`IN LINE`. So which of these settings the unit runs is chosen by software
after fabrication, together with the firmware.

Two protocols use it because a core alone can't keep up:

* **Full-speed USB** (12 Mbit/s at 48 MHz: 4 clocks per bit). The
  pure-firmware receiver takes at least 13 instructions per bit, so it cannot
  keep up ([usb-full-speed.md](usb-full-speed.md), the per-bit budget table).
* **10BASE-T receive** (Manchester at 10 Mbit/s with 802.3's ±13.5 ns of edge
  jitter): the unit samples the pin at both clock edges and tracks the
  sender's phase with a digital loop, while a core sees one sample per clock
  (isa.md §4.7, "Manchester timing"). Every 10BASE-T receiver in `firmware/`
  uses the unit, and none in firmware alone has been written to compare
  with: this is the mechanism, not a measured comparison. What the unit adds
  is the rate and the jitter tolerance, not the coding: firmware alone decodes
  Manchester (bi-phase) at slow rates, in `rc5_rx` (half bit 889 µs) and
  `dali_ctrl` (half bit 416.67 µs), with no `IN LINE`.

Low-speed USB runs either way: on two cores in pure firmware (`usb_ls_rx` +
`usb_ls_tx`) or on one core with the unit (`usb_ls_dev`). The 10BASE-T
transmitter `eth10_tx` does not use the unit.

## Settings exercised by a test

LINECFG fields, from [isa.md §4.7](isa.md#47-line-unit-core-0): `[0]` EN,
`[2:1]` CODE (0 NRZ, 1 NRZI, 2 Manchester), `[5:3]` STUFF_K (0 = off), `[6]`
STUFF_ONES (stuff a 0 after K ones; else the complement after K equal bits),
`[7]` DIFF (a second pin carries the complement), `[9:8]` END (0 release,
1 SE0 then IDLE, 2 high, 3 hold), `[12:10]` END_BITS, `[13]` IDLE, `[14]` RX_EN.
Each row is a value a test writes; the values were decoded from those fields.

| LINECFG | Fields | Protocols | Firmware (where the setting is stated) | Tests that use this value |
|---|---|---|---|---|
| `0x49F3` | EN, NRZI, STUFF_K 6, STUFF_ONES, DIFF, END 1 (SE0, then J), END_BITS 2, RX_EN | USB low speed and full speed: NRZI, a stuffed 0 after six 1s, D+/D− pair, a 2-bit SE0 end of packet. LINEDIV `32 << 16 \| 32 << 8` (low speed) or `4 << 16 \| 4 << 8` (full speed) at 48 MHz | [usb_ls_dev.pasm:5](../firmware/usb_ls_dev.pasm), [usb_fs_dev.pasm:18](../firmware/usb_fs_dev.pasm), [usb_enum.pasm:12](../firmware/usb_enum.pasm); the UART-to-USB pairs load it through `tools/pe/usbenum.py:21` | RTL: `test/test.py::test_usb_fs_device` (line 1333), `test_usb_autonomous` and `test_usb_autonomous_fs` (through `usbenum.load_writes`, line 1231), `test_usb_concurrent_service` (`usb_ls_dev` variant, line 2093), `test/test_usb_uart_rtl.py` (overlap4, envelope3, byte). Model: `test/model/test_usb_enum.py`, `test/model/test_usb_fs.py`, `test/model/test_line.py::test_usb_style_nrzi_stuffing_se0` (checked with an independent USB decoder) |
| `0x4005` | EN, Manchester, RX_EN (receive only) | 10BASE-T receive. LINEDIV `[31:16]` = clocks per bit: 5 at 50 MHz, 4 at 40 MHz | [eth10_rx.pasm:2](../firmware/eth10_rx.pasm), [eth10_match.pasm:3](../firmware/eth10_match.pasm); host side `tools/pe/ethhost.py:35` | RTL: `test/test.py::test_eth10_rx_jitter` (line 1499), `test_eth10_rx_streaming` (line 1889), `test_eth10_common_clock_loopback` (line 1952). Model: `test/model/test_eth10.py` (line 197), `test/model/test_eth10_match.py` (line 58), `test/model/test_faults.py` |
| `0x4E85` | EN, Manchester, DIFF, END 2 (high), END_BITS 3, RX_EN | 10BASE-T receive and transmit on one core: Manchester on a TD+/TD− pair, then TP_IDL (high for 3 bits) | [eth10_reply.pasm:15](../firmware/eth10_reply.pasm); value in `tools/pe/ethreply.py:26` | RTL: `test/test_eth_reply_rtl.py::test_reply_rtl_50mhz`, `test_back_to_back_rtl_40mhz`. Model: `test/model/test_eth10_reply.py`, `test/model/test_line.py::test_manchester` (same value) |

## A setting with a test but no firmware

| LINECFG | Fields | What it is for | Firmware | Test |
|---|---|---|---|---|
| `0x6029` | EN, NRZ, STUFF_K 5, complement stuffing, IDLE 1, RX_EN, single pin | CAN-style framing: after five equal bits, one bit of the opposite level | None. `can_tx` and `can_rx` do their bit stuffing in core code and do not use the unit | Model only: `test/model/test_line.py::test_can_style_nrz_stuffing` (64 bits starting with a run of 10 zeros and one of 11 ones, sent and received back) |

## What the tests cover beyond these values

The RTL line unit is compared with the model's every cycle in the lockstep
test, with random LINECFG values over all three codes
(`test/test_lockstep.py::random_line_writes`), and its bounds are proved
formally ([verification.md](verification.md)). Those runs check that the RTL
does what the spec says for any setting; they do not check a protocol. A
setting not in the tables above (for example END 3, or STUFF_K other than 5
or 6) has no protocol-level test yet.
