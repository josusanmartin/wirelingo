# 072 pkg3-firmware

Branch `ao-pkg3`, on top of 071. Commits: `32b2cee` (the three programs,
their model tests, 6 mutants) and the test.py half of `817c953` (both
programs through the SPI port on the RTL), and `3a7feab` (pe.usbhost: a
DATA toggle that never advances ends the host model's IN loop, claim 5).
Model suite with these tests,
runner1: 3000 passed, 29 skipped at `8c4c635` and again at `3a7feab` (the
branch tip; +40 over 070's run: 18 in test_eth10_fcs, 22 in
test_usb_enum_ep1).

Backlog item AO, firmware part. Evidence levels: **model**, **static
timing checker**, **RTL simulation** (test.py through the SPI port). No
board, no FPGA, no gate level.

Word counts against the 256-word bank (assembled; `test_eth10_fcs.py::
test_tx_fcs_word_count`, `test_usb_enum_ep1.py::test_fits_and_table`):

| Program | Words | Compare |
|---|---|---|
| `eth10_rx_fcs` | 27 | eth10_rx 10, no SFD search or FCS check |
| `eth10_tx_fcs` | 52 | eth10_tx 34, host computes the FCS |
| `usb_enum_ep1` | 255 | usb_enum 253, no data endpoint |

The builder's claims:

1. **`eth10_rx_fcs`** (the v3 study's receiver, by content, on the any-width
   tap: CRCCTRL `0x009F1DB7`, POLY_HI `0x04C1`): finds the SFD itself
   (CMPW against 0xAAAB), queues DA..FCS, and reports FCS good/bad in the
   trailer's bit 0; link pulses and other frames without an SFD leave no
   record. Model: an external sender with 802.3's ±13.5 ns (uniform, and
   at the extremes with −200 ppm), single flipped bits at 4 places (FCS bad,
   length right), 1500 bytes, and core 1 → core 0 with link pulses: all
   pass. The line unit's queue reached 13 of 32 bits at 50 MHz (5 clocks
   per bit). For 50 MHz only (the v3 study's note: at 40 MHz the 6-instruction
   preamble loop could fill the queue).
2. **`eth10_tx_fcs`** (new): eth10_tx with the FCS appended in the engine
   (CRCCTRL `0x00AF1DB7`): the host gives DA..payload and R0 = bits − 1.
   The CRC is preset at the frame start and fed by every `out r3, 1`; in
   the last data bit's three free ticks the CRC goes bit-reversed into the
   OSR and the first FCS bit is taken; FCS blocks with inverted branches
   send its complement, x^31 first; `jmp r1--` counts 32 bits and its
   freed tick closes the FCS loop. Model: minimum frame, streamed 128/69/70/
   71/64-byte frames back to back (part-used last words), a streamed
   1518-byte frame, all decoded from the side-set pins as test_eth10 does
   (Manchester cells, TP_IDL 250–350 ns), and the same wire bits and
   TP_IDL as eth10_tx with the host's FCS for the same frame.
3. **Both ends in the engine**: core 1 eth10_tx_fcs → core 0 eth10_rx_fcs at
   50 MHz (TX divider 1.25) over virtual pins: model (3 frames, with link
   pulses) and **RTL through the SPI port** (`test.py::
   test_eth10_fcs_in_engine_loopback`, 64- and 218-byte frames, quad SPI
   service): every frame arrives with its FCS and the engine's "good".
   The host never computes a CRC.
4. **`usb_enum_ep1`**: the v3 study's autonomous USB device with an
   interrupt IN endpoint 1 (2 bytes: P16–P23, P24–P31), merged by a
   three-way merge with today's usb_enum (its timed J WAITs, the echo
   check, the ACK dribble rule since the study's base). To fit the bank,
   endpoint 1's IN goes through got_in's timed J WAIT and turnaround
   (`flag set F_SENT1; jmp got_in`, back via `jmp f F_SENT1, ep1_data`),
   instead of its own copy; the endpoint-1 block was moved after `se0`, so
   `fin_prep` falls into `echo` again (in the study's file it fell into the
   endpoint-1 filter). 255 words.
   Static checker, full speed (`usb_enum_ep1.timing`): in_ta_any (every
   IN, endpoints 0 and 1, lost-ACK commit) [10, 25] clocks of max 26
   (usb_enum: [10, 21]); ep1_ta [19, 25]; data_ta [9, 18]; in_reach,
   hs_reach, e1_reach ≥ 7 ticks; rx_bit 3 clocks. **Endpoint 1's
   turnaround has 1 clock of margin at full speed (static bound).** The
   model's measured intervals lie inside the bounds (soundness test).
   Model: reports at low and full speed with clock offset and jitter (turn-
   arounds 3.13–5.89 bit times), a lost host ACK repeats the toggle,
   SET_CONFIGURATION 0/1 and absent endpoints, faults mixed over both
   endpoints, bus resets 0–5 clocks after an endpoint-1 IN, and
   reconfiguration back to DATA0. RTL through the SPI port
   (`test.py::test_usb_autonomous_ep1_fs`): enumeration with the endpoint
   descriptor, four endpoint-1 INs (one with a lost host ACK), endpoint 0
   after: **passes at 3 cores and at 2** (`10f07bf`, runner1; turnarounds
   3.25–5.75 bit times, 33 transactions). The FCS loopback (claim 3) passes
   at 3 and 2 cores too.
5. **Mutants** (6): `fw-eth10-tx-fcs-not-reversed`, `-no-preset`, `-31-bits`,
   `fw-eth10-rx-fcs-residue` killed at `8c4c635`; `fw-usb-ep1-toggle-stuck`
   and `fw-usb-ep1-data-as-ep0` killed at `3a7feab` (runner1): **6 of 6**.
   At `8c4c635` toggle-stuck never finished: the host model's `data_in`
   acknowledged and dropped every old-toggle packet without a bound, so a
   stuck toggle looped (45 minutes, stopped by the builder); `3a7feab` gives
   up after max(8, retries) repeats. That loop would also have hung CI's
   mutants job.

The builder's caveats:
- usb_enum_ep1 is not on any board; the KV260 build is AR's. Its full-speed
  margin (1 clock static) is thinner than usb_enum's (5).
- The FCS programs are for 50 MHz (RX) and 4 ticks per bit (TX); the
  host-side `pe.ethhost` stack still uses eth10_rx/eth10_tx and computes
  the FCS; switching it is not part of this item.
- `.data` tables index from the bank's first word; usb_enum_ep1 has
  `.origin 0` (core 0).

Asked checks:
- Read `eth10_tx_fcs.pasm`'s FCS entry and loop tick by tick (each 4-tick
  bit: side values, where the CRC is final, the 32-bit count).
- `remote_test.sh model test_eth10_fcs` / `test_usb_enum_ep1` and the
  two test.py tests (`make TESTCASE=...`, at 3 and 2 cores).
- `python -m pe.timing firmware/usb_enum_ep1.pasm --timing
  firmware/usb_enum_ep1.timing -D DP=0 -D DM=1 -D TURN=4`.
- Mutants `fw-eth10-*`, `fw-usb-ep1-*`: killed.
- Privacy: `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD`
  (0 flagged at `3a7feab`).
