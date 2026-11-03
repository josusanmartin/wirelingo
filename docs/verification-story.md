# Verification story

Wirelingo was built by one person with no chip-design background, using AI
agents. So every result needs a check that does not rest on an agent's word. This
page lists the claims, how each is checked, how far the evidence goes, and what
the checks caught. It is a summary of [verification.md](verification.md),
[review.md](review.md) and [claims-audit.md](claims-audit.md).

State at 2026-11-02, `main` at `61af9b2`. Nothing has been fabricated. FPGA runs
are RTL in FPGA fabric, not silicon.

**Evidence levels:** model, RTL simulation, formal, gate level (GL), gate level
with delays (SDF), RTL in FPGA fabric, silicon.

## Claims

| Claim | How it is checked | Evidence level | Limit |
|---|---|---|---|
| RTL matches the spec's model every cycle | Random programs, pins and host traffic on both; state compared every cycle; coverage goals required | RTL simulation | Finite traces. Same agents wrote both. Covers `pe_chip`, not the SPI port ([verification.md](verification.md) §2) |
| FIFOs are correct, including the join | k-induction against reference queues, depths 4 and 8 | Formal | One block. Says nothing about latch timing |
| Host port does the right thing | k-induction against a pin-level reference; 8 of 8 planted bugs killed ([formal-hostport.md](formal-hostport.md)) | Formal | Inside the host timing contract. No metastability or pad timing |
| Core and line unit stay in bounds | Safety invariants with free inputs | Formal | Safety only. Not functional equivalence |
| Tests catch bugs | 34 of 34 mutants killed, recorded 2026-10-05 ([verification.md](verification.md)) | Mutation: model, RTL, firmware, assembler | Recorded count, not rerun for this page. The list is finite: see the first catch below |
| Netlist equals RTL | 7429 points proved by two engines; 5 planted bugs caught ([equivalence.md](equivalence.md)) | Formal, on run fo6's netlist | Zero delay. Cell insides are not checked |
| Netlist runs the tests | 29 of 29 top-level tests; SDF subsets at three corners, with an overclocked control that must fail | GL, SDF | SDF covers exercised paths. Icarus has no setup or hold checks. The two top-level tests `fault-fixes` added, and its firmware, were not run at gate level |
| 50 MHz at all three corners; DRC, LVS and antenna clean | STA and `flow/release_check.py`, no waiver ([release-evidence.md](release-evidence.md)) | Signoff on run fo6's layout | Models, not silicon. Fast-corner hold margin is 0.095 ns. Receipts stop at software `7685aba` |
| USB works against a real host | Linux enumerated and configured it: 32 of 32 attaches, 66,000 of 66,000 descriptor reads (`GET_DESCRIPTOR` control transfers), 188 of 188 resets ([results](../fpga/kv260/README.md#result-against-linux-low-speed-2026-10-30)) | RTL in FPGA fabric | One host and hub, no scope, 3.3 V FPGA pins. Run with `usb_enum` before `fault-fixes`; the current 253-word one has not run there and needs a rerun before this applies to it |
| Data moves over full-speed USB | 350 of 350 round trips equal, over 2 MB, at 17.6 kB/s | RTL in FPGA fabric | Rate is the Python loop's. 5 of 116 attaches stall, cause not proved |
| Firmware runs on hardware | Most firmware ran with results equal to the cycle model | RTL in FPGA fabric | Far ends are our own models or replayed waveforms. Two-core USB did not run there. SWD is parked. `uart_rx`, `i2c_master`, `can_rx` and `usb_enum` ran in their form before `fault-fixes`; the changed firmware needs a rerun there |
| Quad host port moves 193 Mbit/s | 193 Mbit/s simulated; 193.6 Mbit/s measured on the FPGA | RTL simulation, RTL in FPGA fabric | No board or pad measurement |
| Protocols work on the wire | Pins-only device models; sigrok decoders where one exists | Model, RTL simulation | Digital only. No electrical compliance |
| Firmware survives wire faults | Seeded glitches, stuck lines, truncation, flips, stretch and jitter on six firmware: 576 cases, 0 needs-reset, 1 not `ok` (a second-frame) since `fault-fixes`; a bus reset at every clock of a full-speed control read ([fault-testing.md](fault-testing.md)) | Model; two of the fixes also as RTL tests (`test_uart_rx_glitch`, `test_i2c_master_bus_fault`) | A sample, one fault per case. Not at gate level or on the FPGA |
| 14 protocols added with the RTL frozen | Timing checker, pins-only models, sigrok, 16 RTL tests ([post-freeze-protocols.md](post-freeze-protocols.md)) | RTL simulation, GL, SDF at typical, FPGA | SDF at typical 16/16. Slow and fast 16/16 each in supplementary single-case runs of 2026-11-02 (`5d34fc6`, on `improve-2026-11`, not `main`), outside the release gate, with no verifier report. Two exact assertions got a 1 ns tolerance (`8dd249a`) |
| 10BASE-T receive meets 802.3's ±13.5 ns edge jitter | `test_eth10`, three seeds each; RTL frames with jitter | Model, RTL simulation | No real network. The receive circuit is unbuilt ([eth10-receiver.md](eth10-receiver.md)) |
| `eth10_match` raises a pin 10 to 11 clocks after a field | Model tests; 5 frames on the FPGA | Model, RTL in FPGA fabric | No RTL simulation. Pad and comparator delays unmeasured. Passed on the third fix (report 009), see below |
| Compared with RP2040 PIO | Reference programs on a PIO model and the cycle model ([pio-comparison.md](pio-comparison.md)) | Model | Simulation only. PIO is faster and smaller |
| The chip works | Nothing yet | None | Not fabricated |

## What the checks caught

Each item was written or approved by an agent, then caught by something else.

### Agent claims and process

| What went wrong | Caught by | Reference |
|---|---|---|
| **`eth10_match` had a timing number with no test behind it.** See below | Independent verifier agent, by mutation | Report 000; commit `61af9b2` |
| **The fix then stated a new false number.** See below | The same verifier, on re-attack | Report 001 |
| **The next fix added an "exact" check that could not fail.** See below | The same verifier, by mutation | Report 004 |
| A study's 71-clock host stall budget held for one host and phase only. The verdict's own host survives 59 | The same verifier | Report 002; branch `rmii-study` at `d811228` |
| A network stack's 16 us and 24 us software budgets were measured on 300-byte frames and do not hold on long ones | The same verifier | Report 003; commit `e71ab1c` |
| A Hardcaml port's doc said all planted bugs were caught, while its `self_check.sh` exited 1. Its equality proof to `src/` held | The same verifier | Report 006; commit `03edc75` |
| An agent described the FPGA as running "exactly" and "at full speed" before measuring | A second review; then measured | [methodology.md](methodology.md) line 110 |
| Codex's signoff gate demanded timing Tiny Tapeout does not require, so no run could pass | Claude reviewing the review | [methodology.md](methodology.md) line 107 |
| Codex's reset fix was correct but congested routing | About a dozen place-and-route runs | [review.md](review.md) R3 |
| Signoff claims belonged to older RTL | Codex review; receipts now bind results to hashes | [review.md](review.md) R1 |
| A failed mutation command could count as a kill | Codex review | [review.md](review.md) V4 |
| 46 doc findings: 5 wrong, 6 overclaims, 21 stale, 12 unclear, 2 unsupported. "Two independent implementations" was one | Adversarial claims audit | [claims-audit.md](claims-audit.md), `dc55b1e` |
| A USB test ran at +4.2 % bit rate, and our helpers scaled with it | sigrok rejected the bus | [verification.md](verification.md) line 275 |
| A testbench race took a pad value a clock early under SDF | The negative check in `test_spi_master_fast` | [verification.md](verification.md) lines 354-357 |

### Design, firmware and tools

| What went wrong | Caught by | Reference |
|---|---|---|
| Assembler `/` lost a clock per bit in bidirectional DShot at 50 MHz. Model and RTL ran the same wrong words | The KV260, reading pins with `la_capture` | `2673606`, `1369790` |
| `ws2812` drove a 0 bit for 2 ticks, not 3 | Cycle-model pass of the FPGA run script | `25d3bf6` |
| `can_rx` missed a stuff bit after a CRC ending in five equal bits, about one frame in 30 | The same pass, on a generator frame | `03120ea` |
| Two-core USB firmware took its own ACK for the host's | Concurrent RTL service test | `696d10c` |
| Assembler encoded `nop` with an old field layout | Firmware test on the model | `5861f64` |
| Global reset during SPI left the host port mid-command | Codex review, reproduced as a test | [review.md](review.md) R3; `67fc62d`, `6e7a82b` |
| The timing constraints cut every SCK-to-clk path, so crossings were never timed | Codex review | [review.md](review.md) R2 |
| USB firmware accepted a DATA packet with a corrupt PID | Codex review | [review.md](review.md) R4 |
| A CTRL write put address decode ahead of every core: −7.6 ns at the slow corner | STA path review | `eb70fee` |
| The model used a host write one clock early in FIFO join | Lockstep, on a random mid-run write | [verification.md](verification.md) line 392 |
| Two more model bugs in the line unit | Lockstep, only in seed windows outside the regression's | `a32684f`; [verification.md](verification.md) lines 398-399 |
| A malformed ACK could advance a USB transfer | Later review | [review.md](review.md) follow-up |
| Wire faults: `uart_rx` took a glitch for a start bit, `i2c_master` read a stuck SDA as ACK, `can_rx` sent an error flag for an idle glitch, `usb_enum` missed a bus reset starting in an EOP or a turnaround (then could not be enumerated) | Seeded fault sweep and directed cases; the verifier extended the USB one to 10 ms resets | [fault-testing.md](fault-testing.md); report 014; fixed on `fault-fixes`, model and RTL only |

### The `eth10_match` failure, in detail

On 2026-11-02 an independent verifier agent failed the `eth10_match` work.

* Commit `61af9b2` claimed a reaction of 10.0-10.9 clocks, 201-218 ns.
* The only timing bound in the test was `max(ns) < 500`.
* The verifier added N `mov r1, r1` instructions before `pin HIT, hi`.
* At N = 9 the firmware reacted in 381-398 ns, about double the claim. All 5 tests
  still passed.
* The range itself was the spread of 8 frames. Over 600 frames it was 200-220 ns.
* The "38 us before the frame ends" claim had no assertion.
* Four limits were unstated: silent FIFO loss, threshold wrap, short preambles,
  and no jitter margin at 40 MHz.
* The match decisions themselves were right in every case at 50 MHz.

Reports are numbered by inbox item. They are the project's local verifier records
and are not published with this repository.

**Status of the fixes: passed at `0bdcbe5`, on the third fix.** All of them are
on the local branch `improve-2026-11`, not on `main`.

| Commits | Review | Result |
|---|---|---|
| `7d9377c`, `aedf991` | Report 001 | FAIL. All nine findings were addressed, and delay mutations of N ≥ 1 fail at both clocks. But the new range of 38.2-38.6 us was false for frames ending in a 0 bit: 38.184-38.204 us at 50 MHz. The same fault again: a sample's range stated as a bound |
| `1fddeef` | Report 004 | FAIL. The new "exact" assertion cancelled the rise time out of its own equation, so it passed with the pin 2 us late (N = 100). The doc's ranges held, because the reaction tests guard them |
| `0bdcbe5` | Report 009 | PASS. The rise is bounded directly against the sender's geometry; delay mutations N = 1, 3 and 100 fail, the unmodified firmware passes. The verifier counts the fixes for reports 000, 001 and 004 complete at this commit |

The `eth10_match` numbers on `improve-2026-11` are the ones report 009 passed.
`main` still carries the failed commit's doc and test.

## How an agent's claim gets checked

1. The builder commits on a branch and files the commit, its base and numbered
   claims.
2. A separate verifier agent takes it in its own checkout. It is told to assume
   the claims are wrong. It reads code, tests and docs, not the builder's reasoning.
3. It checks each claim against the diff.
4. It reruns the tests itself. A stated pass count is not evidence.
5. It attacks. It breaks the firmware or the input in a throwaway copy, and a test
   must fail. It checks timing numbers against the ISA, and looks for overclaiming.
6. It writes a report whose first line is `VERDICT: PASS` or `VERDICT: FAIL`. FAIL
   means a claim is false, unproved, or a rule was broken.
7. A FAIL goes back to the builder. The fix gets a new inbox item and a new attack.
8. A PASS merges to the integration branch, never directly to `main`.

Limits of this process:

* It started on 2026-11-02. Earlier work had Codex review, mutation tests and the
  claims audit instead.
* The verifier is also an agent. It shares no reasoning with the builder, but may
  share blind spots.
* It cannot rerun the FPGA. Report 001 could not tell a board log from a model
  transcript, and said so.
* `61af9b2` reached `main` before this process existed. Its fix commits sit on
  `improve-2026-11` without a PASS, which departs from step 8.
