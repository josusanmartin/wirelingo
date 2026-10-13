# USB full speed on protoemu: feasibility study

Branch `usb-fs-study`. The question: can the signed-off chip be a USB
**full-speed** device (12 Mbit/s), and if it can't, what is the smallest RTL
change that makes it possible? This report is meant to support (or stop) a
headline claim, so every number below comes from a run, and the last section
lists what the runs can't show.

> **v2 update.** Change A below (the half-cycle NRZI sample point) is now part
> of the design (branch `v2`, docs/isa.md §4.7), not a patch: the RTL and the
> model always use it. The g4 numbers in this report (§2.1's sweep, the
> "signed-off line unit" lines) describe the chip that was signed off; on v2,
> `test_usb_fs_device` ACKs 24/24 packets at DCD 14 ns (28 ns edge to edge),
> where g4 ACKed 11/24, and the turnarounds are 10.5 / 13.4 / 14.9 clocks.
> Like g4's, these are digital simulations at exactly 48 MHz: whether the v2
> chip runs at 48 MHz is up to its own signoff (g4's slow corner reaches about
> 44 MHz; typ met 50 MHz).

## Bottom line

**The signed-off chip can do it with no RTL change, as far as digital
simulation can show.** One core plus the line unit, clocked at exactly 48 MHz
(4 clocks per bit), runs a full-speed device: it decodes SETUP + DATA0, checks
the CRC16, sends ACK, NAKs an IN when nothing is staged, sends a pre-staged
DATA1, recognises the host's ACK, and enumerates with the unchanged
low-speed reference device software. The same test passes on the model, on
the RTL, and on the **signed-off gate-level netlist** (run g4, netlist sha256
`4db31a01877b1977…`):

| Response | Turnaround, RTL and gate level | Spec (USB 2.0 §7.1.18.1) | Margin to the maximum |
|---|---|---|---|
| ACK after SETUP's DATA0 | 11.5 clocks = 2.89 bit times | 2 to 6.5 bit times = 8 to 26 clocks | 14.5 clocks |
| NAK after IN | 13.4 clocks = 3.34 bit times | same | 12.6 clocks |
| DATA1 after IN (pre-staged) | 15.9 clocks = 3.98 bit times | same | 10.1 clocks |

Over every model run (random phase, jitter, ±2500 ppm, enumeration) the
turnaround stayed between 2.65 and 4.05 bit times.

It works under these conditions:

1. **clk = 48.000 MHz.** At 50 MHz the TX bit time is 4 or 5 clocks (fractional
   divider), which puts 20 ns of deterministic jitter on our edges (spec: ±3.5 ns).
2. **D− on the line unit's pin, D+ on the next pin** (the reverse of the
   low-speed wiring), with the 1.5 kΩ pull-up on D+. With that order the
   receiver sees no edge at the end of an EOP, and the low-speed firmware's
   J/K/SE0 logic carries over unchanged.
3. **Packet sizes the 8-word FIFOs can hold:** IN payloads up to 24 bytes, OUT
   payloads up to 16 bytes. That covers endpoint 0 with `bMaxPacketSize0` = 8 or
   16, bulk endpoints with 16-byte packets, and small interrupt endpoints. For
   64-byte packets the SPI host must stream the FIFOs while the packet is on the
   wire. The model shows that works with quad SPI at SCK = clk/2 and 1 µs of host
   software per transaction, but it is a real-time requirement on the board's
   host.

**The weak point (on g4) is receive jitter margin, not cycles.** The receiver tolerates
an edge-to-edge interval shrinking by about 20 ns (first misses at 20.0 ns at
+2500 ppm, 20.8 ns at 0 ppm). The spec's receiver budget (TJR1) is ±18.5 ns,
which leaves 1.5–2.3 ns of digital margin before pad thresholds, D+/D− skew and
the synchroniser aperture take their share. A one-line change to the line unit
(section 3, no measurable area, no new wires) moves the sample point to the middle of the bit and raises tolerance to
about 31 ns. v2 makes that change. The claim doesn't depend on it.

**Proposed claim wording:** *"A USB full-speed device (12 Mbit/s) on one 48 MHz
core: firmware does PID decoding, CRC16, address filtering and the handshakes
within the bus turnaround, and a small line unit does NRZI, bit stuffing and
clock recovery. Verified in RTL and gate-level simulation; not yet on silicon."*
Three things to avoid saying:

- "Bit-banged." The pure-firmware two-core variant can't reach full speed
  (§2.2).
- "On silicon."
- "0.7 mm²." docs/physical.md gives the tile's core area as 0.90 mm²; if 0.7
  mm² means something else, say what.

## 1. What full speed requires

| Item | Requirement | At 48 MHz |
|---|---|---|
| Bit rate | 12 Mbit/s, device ±0.25 %, host/hub ±0.05 % | 4 clocks per bit, exact |
| Turnaround | 2 to 6.5 bit times from the host's SE0→J to the device's first J→K (7.5 with a captive cable); host times out after 16–18 | 8 to 26 clocks |
| Host inter-packet gap | at least 2 bit times | the next SYNC can start 8 clocks after an EOP |
| EOP | SE0 for 160–175 ns, then J for 1 bit | line unit END=1, END_BITS=2: 166.7 ns |
| Receiver jitter (TJR1 / TJR2) | ±18.5 ns to the next edge, ±9 ns paired | 0.22 bit |
| Source jitter (TDJ1 / TDJ2) | ±3.5 ns / ±4 ns | requires an integer bit time |
| Packets | SYNC, PID, ≤ 64 data bytes (EP0: 8/16/32/64), CRC16, stuffing after six 1s; J = D+ high; 1.5 kΩ pull-up on D+ | |

(The electrical values are USB 2.0 Tables 7-7 to 7-10 as commonly quoted.
Check them against the spec text before publishing.)

## 2. The signed-off chip at full speed

### 2.1 Line unit at P = 4

* **TX:** `LINEDIV = 4<<16 | 4<<8`. The symbol counter reloads INT−1 = 3, so
  every bit is exactly 4 clocks. Stuffing, NRZI and the EOP work as at low
  speed: SE0 for 2 bits = 8 clocks = 166.7 ns, then J for 1 bit, then release.
  Measured on the model's pads: zero edge deviation from the ideal 83.333 ns
  grid.
* **RX:** P = 4 is the documented minimum. A transition loads the counter with
  P/2 − 1 = 1, so each sample is the pad value 2 clocks after the clock edge
  that caught the transition. That is 2 to 3 clocks after the real edge, or 0.5
  to 0.75 of a bit: the sample sits late in the bit. A bit is lost when the next
  edge arrives before the sample, which happens when an interval shrinks by
  more than 83.3 − 62.5 = 20.8 ns. Measured (model, duty-cycle distortion
  sweep, 60 packets per point):

  | Edge-to-edge shrink | 0 ppm | +2500 ppm | −2500 ppm |
  |---|---|---|---|
  | 18.5 ns (spec) | 60/60 | 60/60 | 60/60 |
  | 20.0 ns | 60/60 | 56/60 | 60/60 |
  | 20.8 ns | 60/60 | 17/60 | 60/60 |
  | 24.0 ns | 47/60 | 2/60 | 4/60 |

  Random jitter of ±9.25 ns per edge (±18.5 ns edge to edge) at ±500 and
  ±2500 ppm: 40/40 packets per setting. Drift doesn't matter: seven bits without
  an edge at 0.3 % is 1.75 ns.
* **EOP and the pin order.** With DIFF set, the receiver ends a frame when it
  samples SE0, about 1.5 bits before the SE0→J edge, which helps the
  turnaround. Watch out at the SE0→J edge itself: the receiver starts a frame on
  any edge of RXP. With low-speed wiring (RXP = D+) a full-speed SE0→J raises
  D+, so every EOP would start a junk frame. Putting D− on RXP fixes this: D−
  stays low through SE0→J. It also makes the full-speed pad patterns identical
  to the low-speed firmware's (J = `0b10`, K = `0b01` on {uio1, uio0}), so the
  firmware's J/K/SE0 tests carry over. IDLE stays 0, so LINECFG is the
  low-speed value (`0x49F3`).

### 2.2 Core 0's cycle budget

One instruction per clock (CLKDIV = 1): 4 per bit, 32 per byte.

| Path | Instructions | Budget | Measured |
|---|---|---|---|
| DATA receive (CRC tap is 1-bit IN only, §4.5) | `in line,1` / `jmp t` / `add`, closed by `.wrap`: **3 per bit = 24 per byte** | 32 per byte | 8/24/64 bytes of 0xFF: the loop waits for bits a third of the time, and the line unit never holds more than **3** bits (of 32) |
| DATA transmit | `jmp !osre` / `jmp txne` / `out line,8` / `jmp r0--`: **4 per byte** | 32 per byte | the TX queue (2 entries) never runs dry |
| Token (16 bits after the PID, filter) | `in line,16`, end check, filter compare, PID compares | runs after the frame end | sets the IN/NAK turnaround (below) |
| PID decode before the DATA loop | ~11 | bits wait in the line unit | catches up at 1 bit per 12 clocks |
| Pure-firmware receiver (usb_ls_rx, no line unit) | ≥ 13 per bit (its own tick comments) | 4 | **impossible**: the two-core variant tops out near 3.5 Mbit/s |

The low-speed loop (`in`, `jmp t`, `add`, `jmp`) is 4 instructions per bit
with no slack. The `.wrap` saves the jump.

### 2.3 Turnaround

The ACK path, from the measurements:

* The SE0 starts at time S.
* The receiver samples it 2–3 clocks later. With the 2-flop synchroniser,
  `end_pend` is set at about S + 4.5.
* Firmware takes 12 instructions plus a 2-tick delay to queue the ACK: CRC
  residue compare, PID check, `wait 1 pin` J, then `ldi`/`mov`/`out line`. The
  model shows the push 14 cycles after `end_pend`.
* The line unit starts the next cycle, and the pad changes the cycle after
  that.
* The first K lands at about S + 20. The host's J was at S + 8, so the
  turnaround is about 12 clocks.

The firmware is the critical path: its `wait 1 pin` for J was already true
when reached, so it never stalled. The IN/NAK and IN/DATA paths are anchored on
`wait 1 pin` J, plus 4 ticks (NAK) and the TX underrun checks (DATA). The
delays (`[2]` on ACK, `[4]` on NAK) keep every response at least 2.6 clocks
above the 2-bit minimum. All three responses have at least 10 clocks (2.5 bit
times) to spare against the 6.5-bit maximum.

### 2.4 FIFOs and the SPI host

* **Bandwidth is not the problem.** Quad SPI measured 193 Mbit/s of payload.
  Full speed needs 12 Mbit/s, or 16 Mbit/s of FIFO words with the record
  format's 24 bits per word, plus polling.
* **Latency and depth are.** Both FIFOs are 8 words.
  * IN: the bit count plus 7 words = 28 bytes, so 24 payload bytes can be staged
    whole.
  * OUT: payload + CRC16 goes into 24-bit words, then the partial word and the
    trailer. 8 bytes take 5 entries and 16 bytes take 8.
  * For anything larger the host must move words while the packet is on the
    wire. The budget is 8 words = 21 µs for IN, and 8×24 + 32 bits ≈ 19 µs for
    OUT.
* **Two firmware additions over usb_ls_dev** make that safe:
  * *TX underrun guard.* If a streamed IN finds the TX FIFO empty, the packet is
    cut short (the host sees a bad CRC, sends no ACK and retries) and
    `F_UNDERRUN` is set, so the software re-stages. Without the guard, the
    firmware would wait in `out line` and send the late words as a garbage
    packet.
  * *OUT flow control.* An OUT token that finds fewer than `OUT_WORDS` free RX
    entries gets its DATA NAKed. Without this, a slow host lets the receive
    loop stall on autopush. A stall that loses no bits ends with the ACK late by
    3 clocks per queued bit. That pushes it past 6.5 bit times once 5 bits are
    queued, which risks a collision with the host's next packet. Two
    back-to-back 8-byte OUTs with no draining already hit this (10 entries > 8).
    `test_fs_out_flow_control` shows the NAK, then the ACK once the FIFO is
    drained.
* **Throughput**, measured with the SPI software running concurrently. Each
  register access costs a quad transaction at SCK = clk/2 plus 1 µs of
  software, and the USB host retries NAKs immediately:

  | Packet | IN | OUT |
  |---|---|---|
  | 8 B (pre-staged) | 407 kB/s | 597 kB/s |
  | 16 B | 561 kB/s | 854 kB/s |
  | 24 B | 708 kB/s | 997 kB/s |
  | 64 B (streamed) | 1056 kB/s, 0 underruns | 1259 kB/s |

  The full-speed bulk ceiling is about 1.2 MB/s (19 × 64 B per 1 ms frame); the
  model host sends no SOFs and leaves minimal gaps. How slow the host software
  can be for streamed 64-byte packets (4 packets per point):

  | SPI software per transaction | 2 µs | 3 µs | 4 µs | 6 µs | 8 µs |
  |---|---|---|---|---|---|
  | IN, SCK = clk | 1064 kB/s | 1041 | 958 | 871 | fails (underrun every try) |
  | IN, SCK = clk/2 | 998 | 977 | 921 | fails | fails |
  | OUT, SCK = clk | 1260 | 1008 (1 NAK) | 840 (2 NAKs) | fails | fails |
  | OUT, SCK = clk/2 | 720 (3 NAKs) | 720 | 715 (2 NAKs, 1 lost) | fails | fails |

  Throttling shows up as NAKs from the OUT guard (the previous record is
  still in the FIFO), which costs throughput but no data. A "lost" packet is
  a line-unit overflow mid-packet: bad CRC, no ACK, and the host retries.
  `test_fs_in_underrun_recovers` shows a 20 µs host underrunning and the guard
  recovering. In these sweeps no late ACK was observed, but nothing in the firmware
  rules one out for OUT packets larger than the FIFO. **So: OUT endpoints
  ≤ 16 bytes unless the board host is a dedicated real-time drain.**

### 2.5 Where it breaks first

1. **Receive jitter margin** (§2.1): about 2 ns beyond TJR1 on g4, before
   analog effects; about 12 ns with change A (v2).
2. **Clock:** 50 MHz is out (§2.6).
3. **FIFO depth:** packets over 24 bytes (IN) or 16 bytes (OUT) depend on a
   real-time SPI host (§2.4).
4. **Cycles are not the limit:** 24/32 per byte receiving, 4/32 transmitting,
   and the turnaround has at least 10 clocks to spare.
5. **Pure-firmware (two-core) USB** is 3.5× too slow (§2.2).

### 2.6 48 MHz vs 50 MHz

48 MHz / 4 = 12 Mbit/s exactly, with no TX jitter beyond the clock's. At
50 MHz:

* TX needs INT 4, FRAC 43 (−320 ppm, fine). But each bit is 4 or 5 clocks: the
  model measures edge-to-edge interval errors of −13.3 to +16.7 ns, against a
  ±3.5 ns spec (TDJ1).
* RX can only sample every 4 clocks = 80 ns, a −4 % error.

Digitally it still decodes: the model passed 9/9 settings at ±9.25 ns jitter.
But the transmitter is out of spec, and the RX error eats the jitter margin. A
fractional RX period would fix RX only, so it isn't worth an RTL change. **Run
the chip at 48.000 MHz from a crystal-derived source.**

## 3. RTL changes considered

The design is limited by routing on its one horizontal layer (Metal3, ~68–72 %
in global routing). Small netlist changes have flipped global-routing outcomes
before (docs/physical.md, the host-port reset fix), so even a tiny change means
a full re-harden (3–4 h) and signoff. Ranked by benefit/cost:

| # | Change | Buys | Area | Routing risk | Verdict |
|---|---|---|---|---|---|
| A | **Half-cycle NRZI sample point**: on an NRZ/NRZI transition already seen by the falling-edge sample (`edge1`), load the counter with P/2 − 2 instead of P/2 − 1 | Sample at 1.5–2.5 clocks after the edge instead of 2–3: tolerance **31 ns** instead of 21 (spec 18.5), centred, so margin on both sides | **+8 µm²** on pe_line's 40.8k (yosys + IHP typ lib; 0 flops; within synthesis noise) | None: `rx_mid`/`edge1` already exist inside pe_line for Manchester; no new net crosses a module boundary | **Applied in v2** (was a prototype patch) |
| B | Hardware CRC16 check / byte-wide RX interface | Receive loop at 1–2 instructions per bit | ~16 flops + ~40 gates, plus a status bit into core 0's condition mux | Moderate: a new wire into the core, where congestion lives | Not needed: the loop uses 75 % of its budget |
| C | Hardware token match and automatic ACK/NAK | Turnaround independent of firmware | ~30–50 flops (address/endpoint, state), a config register (host-bus write path) | Moderate–high: the host-bus write path and line unit were the previous congestion sources | Not needed: at least 10 clocks of turnaround to spare |
| D | 16-deep FIFOs on core 0 | 64-byte packets without a real-time host | ~512 more latches (~16k µm²) plus read muxes | **High**: FIFO depth moved Metal3 by ~1.6 points per halving in earlier runs, next to the congested core | Not recommended: streaming works with a responsive host |
| E | Fractional RX sample period | RX at 50 MHz | small | low | Pointless: 50 MHz TX is still out of spec |

### Change A in detail

It was a patch under `` `ifdef PE_LINE_NRZI_HALF `` while `src/` had to stay the
signed-off g4 design; v2 applies it unconditionally (`src/pe_line.sv`,
`tools/pe/line.py`, docs/isa.md §4.7):

```verilog
wire [15:0] r_load = {1'b0, p[15:1]} - (edge1 ? 16'd2 : 16'd1);   // g4: - 16'd1
...  n_rcnt = r_load;   // at a frame start and at each transition (NRZ/NRZI)
```

The spec text: *"a transition sets the sample counter to P/2, or to P/2 − 1 if
the falling-edge sample already showed it."*
For internal pins `rx_mid` equals `pins_in`, so the shorter count always
applies, which is harmless with no analog edge. P ≥ 4 keeps the load
non-negative (`formal/line.sby` asserts it).

Evidence:

* Model sweep, 60 packets per point:

  | Edge-to-edge shrink | 0 ppm | +2500 ppm | −2500 ppm |
  |---|---|---|---|
  | 18.5 ns | 60/60 | 60/60 | 60/60 |
  | 24 ns | 60/60 | 60/60 | 60/60 |
  | 28 ns | 60/60 | 60/60 | 60/60 |
  | 30 ns | 60/60 | 59/60 | 58/60 |
  | 31.2 ns | 60/60 | 20/60 | 30/60 |

* RTL: `test_usb_fs_device` gives 24/24 packets at DCD 14 ns (28 ns edge to
  edge) with the change (now the default). The signed-off g4 RTL gives 11/24.
* Lockstep RTL vs model with the change: 4/4 tests, 60 seeds (rcnt is
  compared every cycle).
* `formal/line.sby` passes (k-induction).
* As a prototype, the full model and top-level RTL suites passed with the
  change on and off (low-speed USB, 10BASE-T receive and the concurrent USB
  service test included), and with it off pe_line.sv was proven equivalent to
  the signed-off file (yosys `equiv_induct`: 811/811 cells). docs/verification.md
  has the v2 regression counts.

## 4. Prototype and evidence

Files on this branch:

* `firmware/usb_fs_dev.pasm`: the full-speed device. It is usb_ls_dev with:
  * the 3-instruction wrapped CRC loop;
  * the ACK decision moved to the front;
  * turnaround delays retuned;
  * the TX underrun guard (`F_UNDERRUN` = flag 0) and OUT NAK flow control
    (`F_NAKOUT` = flag 1, `OUT_WORDS`), on two flags the one-core firmware
    didn't use.

  Host protocol and records are the same as `pe.usbdev`.
* `tools/pe/usbfs.py`:
  * `FsHost`, a pins-only full-speed host. Edges are placed in continuous time,
    with random phase against clk, frequency offset, random jitter and
    duty-cycle distortion. The host decodes with its own clock recovery,
    measures turnaround on the bus, and never drives within the inter-packet
    gap. It doesn't use the firmware or the line model.
  * `SpiService`, device-side host software running concurrently, with quad
    SPI transaction costs from docs/isa.md §6.1.
* `test/model/test_usb_fs.py`, 16 tests:
  * control transaction;
  * jitter/ppm;
  * 2-bit gaps with SOFs;
  * bad CRC/PID/stuffing, foreign addresses, SOF;
  * OUT flow control;
  * enumeration;
  * throughput;
  * underrun recovery.
* `test/test.py::test_usb_fs_device`: RTL / gate-level version (48 MHz, SPI
  configuration, pins-only host with sub-clock edges).

Commands and output:

```
$ PYTHONPATH=tools python -m pytest -s test/model/test_usb_fs.py
turnaround SETUP data -> ACK: 11.9 clocks = 2.97 bit times (spec 2.0-6.5; margin 3.53 bits = 14.1 clocks)
turnaround NAK: 13.7 clocks = 3.42 bit times (spec 2.0-6.5; margin 3.08 bits = 12.3 clocks)
turnaround IN -> DATA/NAK: 15.7 clocks = 3.92 bit times (spec 2.0-6.5; margin 2.58 bits = 10.3 clocks)
jitter +-9.25 ns/edge, +500 ppm: 40/40 ACKed, turnaround 2.65-3.91 bit times
jitter +-9.25 ns/edge, -2500 ppm: 40/40 ACKed, turnaround 2.69-3.87 bit times
enumerated in 0.25 ms of bus time; 14 device responses, turnaround 2.77-4.00 bit times
 8-byte packets: IN 407 kB/s (0 underruns), OUT 597 kB/s (0 unacknowledged tries)
64-byte packets: IN 1056 kB/s (0 underruns), OUT 1259 kB/s (0 unacknowledged tries)
16 passed

$ cd test && make COCOTB_TEST_FILTER=test_usb_fs_device SRC_DIR=$HOME/dev/protoemu/src   # signed-off RTL
turnaround ACK: 2.89 bit times = 11.5 clocks (spec 2-6.5)
turnaround NAK: 3.34 bit times = 13.4 clocks (spec 2-6.5)
turnaround DATA1: 3.98 bit times = 15.9 clocks (spec 2-6.5)
signed-off line unit: DCD 9.25 ns (18.5 ns edge-to-edge): 24/24 ACKed; DCD 14 ns (28 ns): 11/24 ACKed
TESTS=1 PASS=1 FAIL=0

$ make GATES=yes GATE_LEVEL_NETLIST=.../protoemu-exp-g4/runs/wokwi/final/nl/tt_um_josusanmartin_protoemu.nl.v \
       COCOTB_TEST_FILTER=test_usb_fs_device PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3
(identical lines to the RTL run)   TESTS=1 PASS=1 FAIL=0

$ make COCOTB_TEST_FILTER=test_usb_fs_device                                   # v2 (change A applied)
turnaround ACK: 2.64 bit times = 10.5 clocks (spec 2-6.5)
turnaround NAK: 3.34 bit times = 13.4 clocks (spec 2-6.5)
turnaround DATA1: 3.73 bit times = 14.9 clocks (spec 2-6.5)
half-cycle NRZI sample point: DCD 9.25 ns (18.5 ns edge-to-edge): 24/24 ACKed; DCD 14 ns (28 ns): 24/24 ACKed
TESTS=1 PASS=1 FAIL=0
```

The gate-level run is functional (no SDF), the way TT's CI runs it. It shows
the netlist implements the RTL. Timing is covered by signoff STA: typ met at
50 MHz; slow −2.88 ns at 50 MHz, about −2.0 ns at 48 MHz.

## 5. What remains unproven

* **Analog drive.** FS needs 4–20 ns rise/fall into 50 pF, matched rise/fall
  (90–111 %), crossover 1.3–2.0 V and 28–44 Ω driver impedance, normally with
  22–27 Ω series resistors. Nothing is known about the TT IHP pads plus the TT
  mux path at 12 Mbit/s, or about the D+/D− skew through two different pad
  paths.
* **Levels.** USB needs 3.3 V signalling (VOH ≥ 2.8 V); the IO voltage of the
  board path needs checking.
* **Single-ended reception.** The receiver uses D− single-ended, plus both
  lines for SE0, not a differential receiver. That is non-compliant in
  principle, though common in practice (Pico-PIO-USB works this way on RP2040
  GPIO). Threshold offset against the differential crossover comes straight out
  of the ~2 ns jitter margin on g4. Change A (v2) makes this a non-issue.
* **Clock.** It must be 48.000 MHz within ±0.25 %; a crystal-derived PLL clock
  (±tens of ppm) is ample. The open questions are whether the demo board's
  RP2350 delivers 48 MHz to the TT clock pin (e.g. from its 48 MHz USB PLL via a
  GPOUT clock, not a PIO/PWM divider with fractional jitter) and the jitter
  through the TT clock path. None of this is measured.
* **Real hosts.** Only the pins-only model host is tested here. Not covered:
  xHCI/EHCI-companion timing, hubs (FS traffic for other devices; PRE plus
  low-speed traffic, which the receiver should reject as stuffing errors but
  which isn't tested), suspend/resume (3 ms idle, firmware does nothing),
  remote wakeup, and more than one endpoint (the token filter matches one
  address/endpoint; each extra endpoint costs ~3 instructions on the IN
  turnaround, so a handful fit in the 10-clock margin).
* **Board host latency** for streamed 64-byte packets: the model assumes a
  dedicated SPI service loop. A MicroPython host will not keep up. Use
  packets ≤ 16/24 bytes there.
