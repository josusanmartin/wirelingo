# 10BASE-T on a network

The chip moves bits. The host does CRC-32 and the protocols. This page says what
works, at which evidence level, for a station that answers ARP, ping and UDP echo
over 10BASE-T, and what is left for the day the hardware is wired.

Nothing here has run on an FPGA or on silicon, and no cable has been involved.

| Piece | File |
|---|---|
| Ethernet II, ARP, IPv4, ICMP echo, UDP echo, `Station` | `tools/pe/eth.py` |
| Host glue: loads both cores, streams both directions | `tools/pe/ethhost.py` |
| Transmit firmware with carrier sense | `firmware/eth10_tx_cs.pasm` |
| Receive firmware, unchanged | `firmware/eth10_rx.pasm` |
| Timed host port, wire model, pcap writer | `tools/pe/ethsim.py` |
| Bridge from a Linux TAP to the chip | `fpga/kv260/host/eth_tap.py` |
| Tests | `test/model/test_eth_stack.py`, `test/test_eth_stack_rtl.py` |

Pins: TD+ = uo0, TD- = uo1, RXP = ui0, as `eth10-receiver.md` draws it (positive
differential high, idle low). Core 0 runs `eth10_rx`, core 1 `eth10_tx_cs`. 50 MHz
uses CLKDIV 1.25 for TX and 5 clocks per bit for RX; 40 MHz uses CLKDIV 1 and 4.
The MAC is `02:57:4c:00:00:02`, locally administered.

## Evidence

| Claim | Level | Where |
|---|---|---|
| ARP, ping and UDP echo at the pads, 50 and 40 MHz | cycle model, pin level | `test_arp_then_ping_at_the_pads`, `test_udp_echo_at_the_pads` |
| The same, through the real host-port pins: quad SPI, jitter, 1518-byte ping, no reply to a bad FCS or another MAC | RTL simulation | `test_ping_rtl_50mhz`, `test_ping_rtl_40mhz` (ARP and ping, no jitter) |
| The same 50 MHz session, the 40 MHz ping and carrier sense, on run fo6's netlist (a94a948b): 3 of 3 | gate level, functional, no SDF | `flow/gl_module_test.py`, command below |
| Linux's own stack ARPs and pings the chip | real kernel in a namespace, against the cycle model | `eth_tap.py --model --selftest` |
| tcpdump and scapy accept the frames; each flags a broken checksum | cycle model | `test_tcpdump_reads_the_session`, `test_scapy_validates_the_session` |
| No reply to a bad FCS, another MAC, another IP, a bit flipped on the line | cycle model at the pads; bad FCS and another MAC on RTL; all but the flipped bit from real Linux | `test_no_reply_at_the_pads`, `test_bit_flipped_on_the_line_gets_no_reply` |
| 23 kinds of bad frame dropped, each for the stated reason; 6000 random or damaged frames raise nothing | Python, the Station alone | `test_station_rejects`, `test_station_never_raises_and_never_answers_garbage` |
| The chip doesn't transmit into an arriving frame | cycle model; one case RTL and gate level | below |
| Rate limits of the host port | cycle model; four RX edges repeated on RTL | below |

Gate level, on runner1, from a clean checkout with the oss-cad-suite and test venv on
PATH (it only reads the run, and writes a hash-bound receipt):

    flow/gl_module_test.py run ~/protoemu-flow/protoemu-exp-fo6 --validation-root . \
        --module test_eth_stack_rtl --jobs 3 \
        --tests test_ping_rtl_50mhz test_ping_rtl_40mhz test_carrier_sense_rtl

With no delays in that netlist, TD+ and TD- don't change in the same delta, so an
L to H flip passes through both-high inside one timestep: 15,497 of them in those three tests, all settling on H
(`gl/*/sim.log` in the logs below; the test fails otherwise). The first monitor tripped on
that. TD is now read at mid-clock, where it was never both high, and the
test requires every transient to settle on H. A verifier's STA on fo6 has `uo_out[0]`
arriving before `uo_out[1]` at both corners it read (finding 3 of
`protoemu-verify-reports/inbox/003-eth-stack.report.md`; arrivals at the macro's edge,
not a measured overlap), which suggests TD+ rises first on silicon too, and so a short
both-high on each L to H flip there. How long it lasts at the pads is a question for SDF
or a scope. One tick of both-high injected into the firmware does trip the monitor, at this
level and on RTL (the same report, claim 6).

The stack has 21 mutants in `test/mutants.py`, each paired with the test that must
catch it, including a 95-bit window and the IP source and length checks:
`python3 test/mutants.py 'eth-*'`, 21 of 21 killed (`mutants.log` in the logs below).

The far end in the tests shares no code with `pe.eth`. The bits are read off TD+
and TD- by two decoders that work differently, and the FCS is checked with zlib.
The pcap judges see those bytes, so they check our Manchester decode only
in so far as the frames make sense. The second decoder is ours too. The real
check on both is Linux in the TAP run, where the far end is the kernel.

## Half duplex

TD and RX are separate pads and separate pairs, and they run at the same time.
The wire is what is half duplex: a NIC facing a station that sends only link
pulses falls back to 10 Mbit/s half duplex. So the rule is that the chip must not
start while a frame is arriving.

The host can't enforce that. Plain `eth10_tx` starts when the host sets F_GO, and
by then the host's look at LINESTAT is old: an SPI read, an SPI write and the idle
loop. `eth10_tx_cs` makes the decision on the core. With F_GO set it waits for RXP
to go 96 bit times without a transition, then starts the preamble. An edge
restarts the wait.

| | A far-end frame that starts this long before our first edge goes unheard |
|---|---|
| `eth10_tx_cs`, 50 MHz | 130 ns (150 ns is heard) |
| `eth10_tx` with the host checking LINESTAT, quad SPI at 25 MHz | 3930 ns (3950 ns is heard) |

(`test_blind_window_with_carrier_sense`, `test_blind_window_without_carrier_sense`,
both swept in 10 ns steps and asserted exactly.)
The 130 ns is the synchronizer and the jump to the preamble. Any CSMA station has
such a window: inside it neither side can know.

The frame path is the same as `eth10_tx`: same levels in the same order, times
within a clock (`test_cs_firmware_sends_the_same_waveform_as_eth10_tx`).

* With F_GO already set, so that only the firmware can hold the frame back, the far
  end's last edge to our first edge measures 9800 ns at 50 MHz and 9825 ns at 40 MHz
  across the window; 96 bit times are 9600 ns (`test_carrier_sense_defers_to_an_arriving_frame`,
  asserted exactly). A 200-byte case on RTL and at gate level measures 9810 ns: the
  same 9800, read at the next mid-clock (`test_carrier_sense_rtl`).
* A reply queued during an arrival doesn't reach the core until the line is quiet: the
  glue holds it (`test_reply_queued_during_an_arrival_waits`).
* Traffic with gaps under 96 bit times holds the chip off for as long as it lasts,
  and no link pulses go out meanwhile. The glue gives up after 20 ms and restarts
  core 1, which starts with a pulse (`test_line_never_quiet_gives_up_and_link_pulses_resume`
  with a shorter limit).
* The chip's own frames stay apart: F_GO can't be set again until the frame is done,
  and the window runs before the next one (`test_two_chip_frames_keep_the_gap`:
  20.18 us at SCK 25 MHz).

**Collisions are not handled.** There is no jam, no back-off and no retry, and the
chip doesn't notice. If the far end ignores carrier or starts inside the blind
window, both frames run to their end (`test_collision_is_not_handled_but_does_no_lasting_harm`).
Our frame is lost to a real receiver and higher layers retry. No state is left
behind: the test's next ping is answered.

## Can one host stream both directions?

Yes, and on a half-duplex wire it never has to do both at once, except in a collision.
The numbers are for 1518-byte frames with `pe_spim`'s CS timing (2/3/8 clocks) and
no software delay at all: the fastest any host on this port can be. The port is
timed clock by clock, and the four quad and 1-bit RX edges at 50 MHz came out the
same on RTL through the real pins. `test_rate_table`:

| Clock | Mode | RX alone or a reply deferring | TX alone | Both at once (collision) |
|---|---|---|---|---|
| 50 MHz | quad | SCK 7.14 MHz; 6.25 fails | 5.56 MHz; 5 fails | 8.33 MHz; 7.14 loses RX |
| 50 MHz | 1-bit | SCK 25 MHz; 16.67 fails | 16.67 MHz; 12.5 fails | 25 MHz; 16.67 fails |
| 40 MHz | quad | SCK 6.67 MHz; 5.71 fails | 5.71 MHz; 5 fails | 8 MHz; 6.67 loses RX |
| 40 MHz | 1-bit | SCK 20 MHz; 13.33 fails | 20 MHz; 13.33 fails | 40 MHz; 20 fails |

Every pass reads STATUS first: an empty RX FIFO reads 0, and a run of 24 zero
bits makes a data word that is also 0. ARP padding does that. An all-zero 1000-byte
ping is 333 such words in 352 (`test_smallest_frames_and_zero_runs`), so a blind
burst loses data.

The budget a slower host has is the FIFOs, not the wire. This is the software time
allowed per pass with quad SPI at SCK = clk, the longest that passes in 0.5 us steps.
A slower SCK leaves less. `test_service_budget` and `test_service_budget_for_sustained_traffic` assert each
figure and that 0.5 us more fails:

| Case | 50 MHz | 40 MHz |
|---|---|---|
| RX, one 1518-byte frame, alone or with a reply deferring | 16.5 us | 15.5 us |
| RX, frames at the minimum gap: two of 1518 bytes, or twelve of 64 at 50 MHz | 16.0 us | 15.5 us |
| TX, one 1518-byte frame | 22.5 us | 21.5 us |

For one frame, 1518 bytes is the worst size tried. The sweep ran 64, 300, 600, 800, 1000, 1200 and
1518 bytes at 4 phases of the host's passes against the frame (`runner1:~/protoemu-flow/eth-stack/logs/363fc2e/eth-budget.out`);
shorter frames allow more, and only TX at 1000 bytes and 50 MHz moved with phase, by one
step. Sizes between those were not tried. Plan on **15.5 us for RX and 21.5 us for TX**.

A core that is behind when a frame ends takes the bit from TP_IDL's falling edge into
that frame's record, because the line unit's queue has no frame boundaries. The host
drops bits past the last whole byte, as a MAC does, and the FCS decides
(`test_core_behind_at_a_frames_end_adds_a_bit_and_the_frame_survives`). `eth10_rx.py`'s
finite capture, which is separate code, still rejects such a record.

RX has no flow control. A 64-byte frame is 24 FIFO words and the FIFO holds 8, so
even the shortest frame needs service while it arrives
(`test_min_frame_still_needs_service`). Between frames a host can be as late as it
likes, up to the far end's next frame; link-pulse records alone overflowed after
22 unserved pulses (`test_link_pulses_pile_up_if_nobody_reads`), which is about
350 ms at the 16 ms a real far end uses (arithmetic, not a run). So a reply can take
as long as the host wants, and only streaming is tight.

* An overflow loses the frame in flight. It is counted, core 0 is restarted, core 1
  isn't touched, and the next frame arrives whole
  (`test_overflow_loses_one_frame_and_recovers`).
* TX underruns stretch a half cell, and nothing on the chip records one. So a pass
  the FIFOs may not have covered abandons the frame: restart, no TP_IDL, a bad frame
  on the wire, counted. The glue never counts a frame as sent that the far end didn't
  get, at any rate in the table (`test_rate_table`, `test_tx_underrun_abandons_the_frame`).
* While a frame arrives the glue doesn't load a queued reply, and looks at TX every
  eighth pass. Before it did, a reply queued during reception overflowed RX at rates
  where RX alone was fine; `defer` in `test_rate_table` holds it to RX alone's limits.

**Neither real host has been measured.** On the KV260, one pass is two `pe_spim`
transactions from Python, and this is the number that decides whether the bridge
can run on the board:

    sudo python3 fpga/kv260/host/eth_tap.py --measure-spim

A pass may take at most 15.5 us of software plus its SPI time, sustained, on frames up to
1518 bytes (the worst case above, quad at SCK = clk). The command prints software and SPI
together, and a pass here reads 4 words where a late host reads 8, so compare with care:
about 2.8 us of SPI for 8 words at 50 MHz (an estimate from the clock counts) gives
about 18 us per pass as the go/no-go, and it is an estimate built on that tested 15.5 us. It borrows the host port: it moves the pins to
the fabric master and sets QUAD_EN, then restores the select and clears QUAD_EN, its reset
value, since that register can't be read back. That holds on an error, Ctrl-C, SIGTERM and
SIGHUP, and a second signal can't cut it short. With the master stuck busy the select is
still restored but QUAD_EN can't be cleared, and it says so. SIGKILL can't be caught. All of
this is tested against a fake register file, not a board. Core 0 should be idle, because a
FIFO read pops. If Python can't do it, C might: unmeasured. The demo board's bit-banged port is far below the table's lowest SCK (an
estimate); its PIO quad transport may reach the table but MicroPython then has to
make the budget above. `tools/micropython/eth_stack.py` and `eth10_host.py` are the
same code in the board bundle, and they run on MicroPython 1.22's Unix port against
a fake port (`test_runs_on_real_micropython`), on no board.

## Link pulses

They are there, and a host isn't needed for them: the core runs them alone.

| | Result |
|---|---|
| Idle, 50 MHz, both firmwares | 16.000 ms apart, 100 ns wide, positive (`test_link_pulses_while_idle_a_whole_period`); `test_eth10.py` has the same for `eth10_tx` at 40 MHz |
| Across frames | go on, none inside a frame (`test_link_pulses_survive_frames`) |
| `eth10_tx` after a frame | the timer only pauses, so a pulse can come 3.3 us after a frame (`test_eth10_tx_pulse_can_follow_a_frame_at_once`) |
| `eth10_tx_cs` after a frame | timer restarted, R1 = 9998, next pulse 16.0 ms away (`test_eth10_tx_cs_restarts_the_pulse_timer_after_a_frame`) |

The usual figure for how long a receiver waits before dropping link is 50 to 150 ms
(quoted from memory of 802.3 clause 14, not checked against the text here). The longest
silence the firmware can produce is 16 ms, plus a deferral of up to 20 ms when
a peer is the one keeping the line busy.

A far end's autonegotiation bursts, 33 fast pulses 62.5 us apart, arrive as short
records and do no harm (`test_autonegotiation_bursts_from_the_far_end_do_no_harm`).

## Limits

* Frames are 64 to 1518 bytes, destination through FCS, in both directions: an MTU
  of 1500, so 1472 bytes of ping data (`test_largest_ping`, at the pads and on RTL).
  1519 and 1522 bytes (a VLAN-tagged maximum) and 63 and 32 bytes are dropped
  and counted (`test_illegal_sizes_at_the_pads`).
* Half duplex only: no collision handling, as above.
* Unfragmented IPv4 only. Fragments and IP options are dropped or ignored; options
  are read past and not echoed. No TCP, DHCP, IPv6 or ICMP errors.
* TX at 50 MHz has half cells of 40 and 60 ns; at 40 MHz they are exact
  (`test_50mhz_half_cells_are_40_and_60_ns_and_40mhz_is_exact`). RX at 40 MHz is below
  802.3's +-13.5 ns: a ping passes at +-12 ns, 100 ppm (`test_ping_with_edge_jitter`,
  one seed), and at 50 MHz at +-13.5 ns. Neither clock gets both ends right.
* The firmware has no spare core: this takes both, and core 0's line unit.
* `eth10_tx_cs` has run on the model, on RTL and on the netlist without delays, not on
  an FPGA. `eth10_tx` has run on the KV260.

## The TAP bridge

Linux's stack on one side, the cycle model on the other, in a namespace that
disappears with the process:

    sudo unshare --net -- python3 -B fpga/kv260/host/eth_tap.py --model --selftest

The program refuses to run outside a dedicated namespace, or in one holding
any interface but lo (`test_bridge_refuses_the_hosts_namespace`). It never sees eth0,
adds no route outside the namespace, and sets neighbour timers there only. `-B`
keeps root from leaving `__pycache__` in the checkout.

Run on runner1 from a clean clone of `400b294`, whose code is the same as this commit's,
inside `unshare --net`, once per clock (logs: `eth-tap-50.log`, `eth-tap-40.log` in
`runner1:~/protoemu-flow/eth-stack/logs/363fc2e`):

| | 50 MHz | 40 MHz |
|---|---|---|
| ping, 56 bytes of data | 5 of 5 | 5 of 5 |
| ping, 1472 bytes, DF set | 2 of 2 | 2 of 2 |
| UDP echo from a socket | answered | answered |
| No reply: unknown IP; chip's MAC with another IP; chip's IP with another MAC; bad FCS | all four | all four |
| Then a ping is answered again | yes | yes |
| Wall-clock round trip, short ping, average | 9222 ms | 4221 ms |
| Simulated round trip, short ping | 208-1110 us | 215-1129 us |
| Simulated round trip, 1472 bytes | 3.8-5.0 ms | 3.8-5.0 ms |

The wall-clock figure is the speed of the Python model on a shared machine, not of
anything else: the two clocks ran side by side, and the figure moves from run to run.
The simulated one starts at the request's first edge and ends
at the reply's last, at quad SCK 25 MHz, and includes frames that Linux queued behind
each other because the model is slower than ping's interval, so it changes from run
to run too. Neither is a latency of the chip. Simulated time stops when nothing is in
flight, so the chip's link pulses barely run in this bridge. Linux's IPv6 chatter
goes through too and is dropped by the Station's address check.

To drive it by hand: run the same command without `--selftest`, and ping
10.77.0.2 from a shell in that namespace (`nsenter -t PID -n`).

### The FPGA backend

`eth_tap.py` takes a backend with `send`, `poll` and `busy`. `ModelBackend` is one;
`FpgaBackend` is a stub that exits. To fill it in:

1. `--measure-spim` first. `Eth10Host` then runs over `pe_spim.Spim` unchanged.
2. A way into RXP: the pattern generator (`pe_lab`) can play a frame, as
   `legacy_demo.py eth10_rx_gen` does, but its table holds 1024 entries, enough for
   a 64-byte frame and not a 1518-byte one.
3. A way out of TD: no core is free to sample it, since this stack takes both.
   That needs a fabric capture or a wire.

With a real cable none of that applies and the bridge isn't needed: Linux on the other
end of the cable is the test.

## What remains for hardware

* TD: series resistors and magnetics, and a real receiver's view of 40/60 ns half
  cells. Unmeasured.
* RX: the front end in `eth10-receiver.md` is unbuilt. Its idle-low output is what
  carrier sense relies on: a comparator that chatters at idle would hold the chip
  off until the 20 ms limit, and one stuck at a level would hide carrier.
* Polarity: a swapped pair inverts the bits. The receiver expects idle low and a
  first rising edge, and nothing here corrects it.
* Whether a real switch or NIC accepts these link pulses and falls back to 10 Mbit/s
  half duplex by parallel detection.
* TX crosstalk into RX on a real board. In the model nothing loops back.
* The real host's rate, above, and pad timing at the SCK it uses.
