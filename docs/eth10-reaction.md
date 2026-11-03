# 10BASE-T match, then answer

`firmware/eth10_reply.pasm` hears a 10BASE-T frame, compares a field in it while it
arrives, and when the frame has ended and the line has been quiet for 96 bit times,
sends a reply frame on TD+/TD-. One core does both directions, and no host is involved
after the program is loaded. This is a 10 Mbit/s bench demo: a bit takes 100 ns.

Evidence: the cycle model (`tools/pe/sim.py`, lockstep-checked against the RTL) and
RTL simulation (cocotb, Icarus). Nothing here has run on an FPGA, at gate level or on
silicon, and no cable or real network peer has been involved.

## What the chip allows, and what was built

| Question | Answer, from the code |
|---|---|
| Cores | 2 in the IHP build (`NCORES=2`, `NBANKS=2`, 8-word FIFOs: `src/project.v`, isa.md §7) |
| Program memory | 256 words per core, in the core's own bank only (isa.md §3). `eth10_reply` takes 161 |
| Who can transmit Manchester | The line unit, core 0 only, both ways at once: RX clock recovery and TX with DIFF (TD- = !TD+) and TP_IDL (`END=2`) from one LINECFG (isa.md §4.7). TX takes up to 32 bits per `out line`, two entries queued |
| Hand-off between cores | 8 global flags and 8 virtual pins; no interrupts. FIFOs connect a core to the host only, so one core can't hand frame bytes to another |
| CRC-32 | None: the CRC tap is at most 16 bits wide (isa.md §4.5). The chip can't compute an FCS |

So one core can match and answer, and no hand-off is needed: core 0 hears through the
line unit, then sends through it. The FCS rules out a reply computed from the frame
being answered, so the reply is a fixed frame whose FCS is computed once on the PC
(`tools/pe/ethreply.py`) and built into the program: 32 `ldi`/`mov osr`/`out line, 16`
triples, 3 words per 2 bytes. It is preloaded in that sense: it changes only by
reloading the program.

The alternative, core 1 running `eth10_tx_cs` and core 0 setting its F_GO flag, was
not built. The reply would have to sit in core 1's TX FIFO (16 words joined: exactly
one 64-byte frame), a send empties it, and only the host can refill it: one reply per
host visit. Core 1 is unused here.

## What it does

Core 0, CLKDIV 1, the line unit as `eth10_match` uses it plus its transmitter:

1. Hunt for the SFD, read 96 bits of addresses, compare the EtherType (0x88B5), then
   the 16-bit field in the first two payload bytes (little-endian) with the threshold
   the host pushed at load. Field >= threshold: HIT (uo2) rises, one more reply is
   owed (counted in r0), the field goes to the RX FIFO (`push noblock`; nobody has to
   read it). The rest of the frame is drained to its end. This path is `eth10_match`'s.
2. With a reply owed, at a frame's end: wait for RXP to be low (the far end's TP_IDL
   over), then for 96 bit times without a transition. Any transition in that window is
   carrier: the core goes to hear that frame, and the wait starts again after it.
3. After 96 quiet bit times: the 64-bit preamble and SFD, then the 64-byte reply, as 36
   sixteen-bit chunks to the line unit; it ends with TP_IDL, 300 ns high (the reply's
   last bit is 0, `END_BITS = 3`). TD+ = uo0, TD- = uo1, RXP = ui0.
4. After the last chunk, wait until RXP has been quiet for 40 bit times, which outlasts
   the rest of our own frame (about 35 bit times). Then the next owed reply waits its
   own 96-bit window, or the core hunts again.

The default reply (`ethreply.reply_frame()`) is a UDP/IPv4 broadcast from
02:57:4c:00:00:02 / 10.77.0.2 to 10.77.0.255 port 7777 with 18 bytes of data,
`wirelingo matched!`; the IP identification is the first value that makes the FCS's
last bit 0 (it is 2).

## Measured

N is the reply's first TD edge minus (the far end's last RXP edge + 9600 ns), in
clocks. The far end's last edge is the end of its TP_IDL; 802.3's 96-bit gap counts
from the end of its last bit, which comes TP_IDL earlier, so measured from there the
gap is longer by the sender's TP_IDL (300 ns in pe.ethsim).

| Claim | 50 MHz | 40 MHz | Level | Where |
|---|---|---|---|---|
| N, far end's TP_IDL 250-350 ns, +-100 ppm, every sampling phase | 0 <= N < 1 clock (measured 0.003-0.994 over 31 replies) | 0 <= N < 1 (0.024-0.926 over 38) | model | `test_match_then_reply_timing`, `test_other_tp_idl_lengths` |
| End of the far end's last bit to our first edge (300 ns TP_IDL) | 9900.1-9919.9 ns | 9900.6-9923.2 ns | model | same |
| The same with 802.3's +-13.5 ns edge jitter | 0 <= N < 1 (0.025-0.779, 12 replies) | not run (RX at 40 MHz has no margin for that jitter) | model | `test_reply_with_8023_jitter` |
| One valid reply per matching frame, none otherwise, no host access after loading | 60 frames, 31 replies | 60 frames, 38 replies | model | `test_match_then_reply_timing` |
| N on RTL at the pads (the monitor reads half a clock later; the test asserts 0.5 <= read < 1.5) | 0.12, 0.24, 0.58: 3 replies, +-13.5 ns jitter, -200 ppm | 0.00: the far end's last edge fell on a clock edge (back to back, 0 ppm) | RTL simulation | `test_eth_reply_rtl.py` |
| Frames back to back at 802.3's 96-bit gap: all heard, every match answered after the train | 2-5 frames | 2-5 frames | model; 3 frames on RTL at 40 MHz | `test_back_to_back_frames_are_all_heard`, `test_back_to_back_rtl_40mhz` |
| Our TP_IDL's end to our next reply's first edge | 10140 ns | 10175 ns | model; 40 MHz also RTL | same |
| A far-end first edge this long before ours goes unheard (collision); 5 ns more is heard | 95 ns (5 clocks: 100 ns is heard) | 120 ns (5 clocks: 125 ns is heard) | model | `test_blind_window` |
| A frame arriving while we reply (1, 25, 50, 56 us after our first edge): our reply whole, that frame not heard, the next one heard and answered | yes | yes | model | `test_frame_arriving_while_replying` |
| A far-end frame whose first edge comes up to this long after our TP_IDL's end is not heard; 5 ns later is | 480 ns | 500 ns | model | `test_frame_just_after_our_reply` |
| Far-end link pulses inside the window restart it; the reply then starts N clocks after 96 bit times from the last pulse | N = 2.98 | N = 3.99 | model | `test_link_pulses_restart_the_window` |
| A TP_IDL of 60-200 ns delays the reply, never brings it earlier | N up to 11.96 | N up to 10.52 | model | `test_short_tp_idl_only_delays_the_reply` |

Each reply is decoded off the pads by two decoders of one capture that must agree (pe.ethsim's edge
tracker, `test_eth_stack.sampled_decode`'s cell sampler; both read the same edge list, so a
capture or stamping error would reach both), with exactly 802.3's
preamble and SFD, a TP_IDL of 250-350 ns, TD+ and TD- never both high, and the FCS
checked with zlib; `test_reply_frame_is_valid_and_fits` checks the IPv4 and UDP
checksums with code that shares nothing with `pe.eth`. sigrok has no 10BASE-T or
Manchester decoder (libsigrokdecode 0.5.3's list was checked), so it isn't used here.

### Where N's bound comes from

The bound is derived, then each piece is checked per reply on the model:

* the synchronizer: a pad edge reaches the cores 2 to 3 clocks later, counted to the
  start of the cycle that sees it (isa.md §2: two flops, plus the edge's phase); the
  low wait completes in that cycle;
* the firmware: pe.timing's `ifg` deadline, from the low wait's completion to the first
  `out line` completing, is exact: 477 clocks at 50 MHz, 381 at 40 (IFG_TICKS + 5);
* the output: TD changes at the clock edge after the one ending the cycle `out line`
  completes in (the line unit takes the entry in the next cycle, and its outputs are
  registers; isa.md §4.7): 1 clock.

2..3 + (96 bit times - 8 + 5) + 1 - 96 bit times = [0, 1) clock. `WINDOW_TRIM = 8` in
`pe.ethreply` is that sum. The bound is half-open only in simulation: a real synchronizer
whose input changes on a clock edge can resolve either way, so on silicon it is [0, 1];
N >= 0 is unaffected. The blind window is the same chain the other way: a far-end
edge is heard if the core sees it by the tick the window's wait times out, 3 clocks
before our first edge, and the synchronizer adds 2: 5 clocks.

**Model time and pad time.** `pe.ethsim.Wire` samples RXP at a clock edge but stamps a
TD change one clock later, at the end of the model cycle that made it. The model tests
move TD times back by that clock (`WIRE_STAMP`). The first version of this firmware was
tuned on the Wire's stamps and came out a clock early on RTL: the RTL test read N at
-0.42 and -1.0 clock at the pads, before the 96 bit times had passed. The RTL numbers
above are from the corrected firmware. The same stamping is in the numbers
`docs/eth10-network.md` quotes from the model; the RTL check there (9810 ns) used one
phase only, and this was not looked into further.

## Limits

* **No link pulses.** Neither core sends 802.3's normal link pulses, so a real NIC or
  switch would see the link as down. Core 1 is free for it; not built.
* The reply is fixed: the same 64 bytes for every match. Changing it means reloading the
  program with the core disabled. The program has room for about 31 more chunks, a
  reply of up to about 126 bytes (arithmetic, not built). Its last bit must be 0
  (`pe.ethreply.chunks` refuses otherwise): after a 1, three bit times of TP_IDL would
  be 350 ns and two 240 or 260 ns at 50 MHz.
* Speculative, as `eth10_match`: the decision comes before the FCS, so a corrupted frame
  can be answered. One field at a fixed place, one threshold set at load (16 bits).
* Half duplex without collision handling: no detection, jam, back-off or retry. A frame
  that arrives during our reply, or starts inside the 5-clock blind window, is lost to
  us and our reply goes out whole. A far end that keeps the 96-bit gap is never affected
  by the 480-500 ns after our TP_IDL in which a frame is not heard.
* The window is counted from the far end's last edge at the pad, not from a real PHY's
  carrier-sense deassertion, which comes later. A far-end TP_IDL shorter than about
  250 ns ends before the core reaches the low wait, and the reply comes later.
* Owed replies are counted in 16 bits, and a far end that never leaves 96 quiet bit
  times holds them back indefinitely (no give-up timer, unlike `pe.ethhost`'s 20 ms).
* Records: the RX FIFO keeps the first 8 matched fields if nobody reads it; HIT and the
  replies don't depend on it.
* 40 or 50 MHz only. At 40 MHz receive has no margin for 802.3's +-13.5 ns jitter
  (inherited from the line unit, guide.md); the 40 MHz runs have no jitter.
* Pad, comparator and magnetics delays are not in any number here. They only add delay
  on both sides, so they can make N larger, not negative.

## Reproduce

From a checkout of this branch, on a box with the test venv (the author's rule: not
the shared local box):

    PYTHONPATH=tools python -m pytest -q -s test/model/test_eth10_reply.py
    cd test && make COCOTB_TEST_MODULES=test_eth_reply_rtl SIM_BUILD=sim_build/test_eth_reply_rtl
    python3 test/mutants.py 'reply-*'
    PYTHONPATH=tools python -c "from pe import ethreply, timing; \
      c, r = timing.check_source(ethreply.FW.read_text(), ethreply.defines(50e6)); \
      print('\n'.join(x.report() for x in r))"

Logs of these runs (model file and RTL at c1b1733, the mutants at c1b1733 and the
retargeted `reply-wire-phase-ignored` at baa2d3d) are in
`runner1:~/protoemu-flow/reaction/logs/`. Each new check has a one-line mutant in
`test/mutants.py` (`reply-*`): 15 of 15 killed.

The register writes from reset to running are `pe.ethreply.load_writes(clock_hz,
threshold)`; the reply is `pe.ethreply.reply_frame()`.
