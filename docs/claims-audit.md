# Claims audit (2026-10-14)

*Historical record: it describes the repository as it was on 2026-10-14 and
is not kept up to date. Later state is in [rules-check.md](rules-check.md)
(2026-11-03) and the docs it points to; where a recommendation below has
since been acted on, it says so.*

An adversarial read of what the documentation claims against what the
repository's evidence shows, as a skeptical judge would do it. Base: `main`
at `0c459b9`. Physical candidate: run fo6, tags `rc-d916d40-fo6` and
`rc-f1cb7c9-fo6`. `main` has changed firmware, tests and tools since
`f1cb7c9`; `src/*.v` has not changed since `rc-93c2137-t48ba`.

Scope: README.md, docs/info.md, methodology.md, physical.md,
release-evidence.md, verification.md, review.md, guide.md,
post-freeze-protocols.md, usb-*.md, spi-packed-host.md,
uart-capture-host.md, formal-hostport.md, uart-rx-timing.md,
timing-check.md, board-transport.md, eth10-receiver.md, isa.md (one
sentence) and fpga/kv260/README.md.

Severities:

* **wrong**: contradicted by the repository.
* **overclaim**: stronger than the evidence.
* **stale**: true once, not now.
* **unclear**: a judge could misread it, or it understates the evidence.

## How it was checked

* **Word counts.** Every `firmware/*.pasm` was assembled with `tools/pe/asm.py`, using the
  tests' defines where sizes depend on them. Every word count cited in the
  docs matches, including the round totals: 697, 398 and 734 words. The
  240 and 241 words in the KV260 logs are dated transcripts of an older
  `usb_enum`, labelled as such; it was 246 words at this audit (253 since
  `fault-fixes`).
* **Test names and counts.** Every cited test name exists. Counts were
  taken by grepping `@cocotb.test` and `def test_`: test.py has 25 (also at
  both release tags), test_pio 2, test_eth_service 2, test_post_freeze 16,
  test_sigrok 8 and `test/mutants.py` 34. `flow/gl_test.sh` and
  `flow/release_receipts.py` run exactly test, test_pio and
  test_eth_service at gate level.
* **Links.** All relative links and the 41 anchors resolve. Missing
  backticked paths are each explained in place: removed scripts, generated
  outputs, an external repository, the unmerged `v3-study` branch.
* **Mutants and formal targets.** `PYTHONPATH=tools python -m pe.mpy_bundle --check` passes. The
  `formal/Makefile` targets cited in the docs exist.
* **Git history.** Checked: the tags, and `git diff` of `src/`, `firmware/`,
  `test/` and `tools/` since each tag. Also the commit counts in
  methodology.md (184 commits at 19ebe98, 164 co-authored by Claude: correct)
  and the first commit's date (2026-09-30, as the author says).

## Findings

| # | Sev. | Where | Claim | Evidence | Action |
|---|---|---|---|---|---|
| 1 | wrong | README, post-freeze bullet | "`src/` unchanged since" `rc-93c2137-t48ba` | `src/config.json` changed (f6b8ca0, fo6's antenna repair). `src/*.v` is unchanged | Fixed: "the chip's Verilog `src/*.v` unchanged since; only `src/config.json` changed for fo6" |
| 2 | wrong | post-freeze-protocols.md:9 | `git diff rc-93c2137-t48ba -- src/` is empty; `tools/pe/{asm,timing}.py` "as on `main`" | the same `config.json` change; `asm.py` `/` became integer division (2673606); `timing.py`'s command line changed (34de7a0) | Fixed: past tense, both changes named |
| 3 | wrong | README, "Physical design" | "44 % standard-cell utilization, 50 % with the macros", next to "47 % … 53 %" for fo6 | 44/50 % are g4's and v2c's numbers (physical.md); t48ba and fo6 are 47/53 % | Fixed: the stale figure removed |
| 4 | wrong | README, "What runs on it" key | "RTL" rows: "the same suites pass on the signed-off gate-level netlist" | gate level runs only test, test_pio and test_eth_service. test_post_freeze, test_usb_uart_rtl, test_spi_packed_host and test_uart_capture_host have never run on a netlist | Fixed: only the main suite is gate level; the others are RTL only |
| 5 | wrong | formal-hostport.md:113 | `pe_hostport.sv` "as taped out" | nothing has been taped out | Fixed: "as in the release candidate, run fo6 (not yet taped out)" |
| 6 | overclaim | README, intro | "every claim backed by evidence…: two independent implementations of the spec" | methodology.md itself says the same agents wrote both ("two languages don't make two minds"); this audit found stale and unsupported claims | Fixed: "claims checked against evidence…: two implementations of the spec (a Python model and the RTL)" |
| 7 | overclaim | verification.md §1 heading and text | "two independent implementations" | as #6 | Fixed: heading "two implementations", plus the shared-author caveat and what covers it |
| 8 | overclaim | README, release bullet | "reproduces its netlist and GDS bit for bit" | physical.md: identical netlist; GDS identical only once the BGNLIB/BGNSTR timestamps are zeroed | Fixed: "netlist bit for bit, and its GDS apart from the timestamps" |
| 9 | overclaim | fpga/kv260/README.md:3 | "Hardware validation of the chip RTL … before silicon" | FPGA fabric, inferred RAM, FPGA pads and timing; not the IHP netlist, its pads or the TT mux | Fixed: says what it validates and what not, links "FPGA vs ASIC differences" |
| 10 | overclaim | spi-packed-host.md:159 | "This proves bounded capacity/silence" | one finite run | Fixed: "This run shows" |
| 11 | overclaim | README, "Where it stands" | "Measured against the RP2040 PIO's reference programs" | pio-comparison.md: cycle model and a PIO model, no hardware | Fixed: "Measured in simulation" |
| 12 | stale | README, release bullet | "Release candidate: run fo6 (software `d916d40`)" | a second recorded release, `f1cb7c9` (tag `rc-f1cb7c9-fo6`). `main` has since added the round-3 firmware and fixed `ws2812` and `can_rx`; neither receipt covers them (release-evidence.md: any change to `firmware/` needs a new run) | Fixed: both validation roots named, and the release says it does not cover `main` |
| 13 | stale | release-evidence.md, `f1cb7c9` record | does not say that `main` has moved past it | as #12 | Fixed: one sentence; GDS hashes labelled raw versus timestamp-normalized (the tag message gives `5e0e3c6e`, the doc gave `e6769703`) |
| 14 | stale | physical.md, status and fo6 paragraph | only the `d916d40` receipt | `f1cb7c9` passed too (2026-10-14) | Fixed |
| 15 | stale | README, v2 bullet | "(signed off as run v2c) … v2c meets 48 MHz at the typical corner, slow about 46 MHz" | fo6 has the same v2 RTL and meets 50 MHz at all corners | Fixed |
| 16 | stale | guide.md, "routed netlist" row | v2c: 24 tests; SDF at typ/50 and slow/43.5 MHz, control at slow/80 MHz | fo6: 29/29 functional; SDF at typ/slow/fast 20 ns with a slow/10 ns control; v2 set and autonomous USB at all three corners (`flow/gl_sdf_test.sh`, release-evidence.md) | Fixed |
| 17 | stale | guide.md, "Physical signoff" row and "Known limits" | v2c, slow ≈ 46 MHz, fast hold +0.04 ns | fo6: 50 MHz at every corner, fast hold +0.095 ns, release gate passes | Fixed |
| 18 | stale | guide.md, "What is new" | "formal proofs of the FIFOs" | also host-port safety and its reference-model proof, core and line unit | Fixed |
| 19 | unclear | guide.md, claims table | the reviewer's claim map had no row for the FPGA or the post-freeze protocols | fpga/kv260, post-freeze-protocols.md | Fixed: two rows; post-freeze marked RTL-only, not gate level |
| 20 | stale | verification.md §4 | concurrent USB tests: "Functional GL can run these same tests, but requires a new candidate netlist" | they are in test.py, 25/25 at gate level on fo6 | Fixed |
| 21 | stale | verification.md §4 | test_eth_service passes "on the retained global-routing checkpoint netlist" | 2/2 on fo6's final netlist | Fixed |
| 22 | stale | verification.md, sigrok | "test_sigrok # 6 tests"; list lacks autonomous USB | 8 tests (`sigrok_usb_autonomous`, `_fs`) | Fixed |
| 23 | stale | verification.md §5 | SDF: five tests at typ/50 and slow/43.5 MHz, control at slow/80 MHz; v2 at slow 23 ns; "g4 and v2c pass" | `flow/gl_sdf_test.sh` now: typ, slow and fast at 20 ns, control at slow/10 ns; v2 and USB FS at every corner; fo6 passes | Fixed; g4 and v2c kept as history |
| 24 | stale | info.md (datasheet), firmware list | ends at DShot600, "verified in simulation" | 14 post-freeze protocols and the reverse-engineering pair exist; most firmware also ran on the KV260 | Fixed: list completed, FPGA scope stated (SWD and the two-core USB device have not run there) |
| 25 | stale | info.md, autonomous USB | "in simulation of the KV260 FPGA build" | it ran on the KV260 against a replayed host, 16/16 | Fixed |
| 26 | stale | review.md, ACK follow-up | "do not qualify the updated ACK paths on native RTL, a board or silicon" | the KV260 `ack` scenario ran the malformed ACKs at both speeds on the FPGA build. No RTL or gate-level simulation covers them (none in `test/*.py`) | Fixed: says exactly that |
| 27 | stale | review.md, "Still open" | the KV260 list stops at UART and quad SPI | replayed-host USB and the post-freeze protocols ran since | Fixed |
| 28 | stale | methodology.md, record sections (not the author's statement) | "Hardware" bullet; sigrok list; "Limits": pads and latch FIFOs "only covered by static timing analysis" | KV260 ran much more; sigrok decodes the post-freeze protocols; SDF gate level also covers exercised paths | Fixed, minimal; the author's section is byte-identical |
| 29 | stale | usb-enumeration.md | "Not run: gate level"; "one word (241)"; "65 tests" | fo6 receipts (`f1cb7c9`, current `usb_enum`) run both autonomous tests with SDF at all corners; 246 words; 71 tests | Fixed |
| 30 | stale | usb-full-speed.md (4 places) | 48 MHz "up to its own signoff"; g4 STA as current; "16 tests"; 135/241 words as current | fo6 meets 50 MHz; 19 tests; 141/246 words | Fixed |
| 31 | stale | uart-capture-host.md, uart-rx-timing.md (4), usb-uart-coexist.md | "pending integration", "integrate those files first" | all on `main`; coexist re-run at 0c459b9: 3 passed | Fixed |
| 32 | stale | post-freeze-protocols.md:47 | round-1 model tests: 78 | 79 since the DShot test is parametrized at 48 and 50 MHz | Fixed |
| 33 | stale | fpga/kv260/README.md, USB section | full speed "not simulated on this design" | it has since run against the replayed host (not a real one) | Fixed |
| 34 | unclear | README, "Not yet on … silicon" bullet | one line, fifth bullet | — | Fixed: says what levels the evidence is at, and that no protocol has met a real external device or host. Placement: recommendation A |
| 35 | unclear | README, post-freeze bullet | "added in firmware alone"; KV260 results "equal to the cycle model's" | one assembler fix was needed; the far ends were our own, mostly pre-timed, models | Fixed |
| 36 | unclear | README, KV260 and USB rows; usb-enumeration.md | "every device edge at the clock the cycle model gives" | true with the model sampling pads as the FPGA drives them; with its default, full-speed answers come one clock later (fpga/kv260/README.md, "Pads") | Fixed: qualifier added |
| 37 | unclear | README tables, "Verified on" | KV260 missing for uart, `spi_master_fast`, `i2c_master`, `la_capture`/`i2c_device`, the round-1 rows, packed SPI and UART capture | all ran there (fpga/kv260/README.md) | Fixed |
| 38 | unsupported | README, verification.md, isa.md | eth10 RX "test_eth10's sweep: 30/30 frames at ±15 ns, 9/30 at ±18 ns" | no test runs ±15 or ±18 ns; the numbers are a one-off model sweep recorded in commit 19707ef (2026-10-03), before v2 changed the line unit | Fixed: labelled a one-off sweep, regression scope stated |
| 39 | unsupported | guide.md, "Known limits" | eth10 at 40 MHz: 0/100 at ±12 ns, 27/100 at ±13.5 ns | no test reproduces it | Fixed: labelled a one-off sweep |
| 40 | unclear | README, Metal3 | 64 % in one bullet, ≈ 63 % in another | t48ba/fo6 global routing: 63.9 % | Fixed: 64 % in both |
| 41 | unclear | usb-uart-byte.md, usb-uart-coexist.md | `evidence/sol-*` paths | not in the repository or on this machine | Fixed: marked "not part of this repository" |
| 42 | unclear | verification.md, bug table | the two firmware bugs and the assembler bug found during the KV260 work are not listed | fpga/kv260/README.md: `ws2812` T0H, `can_rx` stuff bit (both found by the run script's model pass), the assembler `/` truncation (found on the board) | Fixed: three rows |
| 43 | unclear | methodology.md:37 | "The author worked with Claude Opus 5.5 and Astra" | the rest of the page and review.md name Codex as the reviewer; "Astra" appears nowhere else. Committed with the author's statement | Not edited: recommendation B |
| 44 | unclear | usb-uart-byte, usb-uart-coexist, spi-packed-host, uart-capture-host, uart-rx-timing | internal ticket jargon (C13–C24, R13, "Atlas", "settlement ticket", "lead-coordinated") | a judge can't parse it | Not edited: recommendation C |
| 45 | unclear | usb-uart-byte.md:202-208 | C23 numbers "do not imply a C24 result before that check completes" | could not tell whether C24 completed | Not edited: recommendation C |
| 46 | unclear | host-workflow and USB docs | missing spaces between words and numbers ("uses19 words", "is135") | typography | Fixed only on edited lines: recommendation D |

**Counts:**

| Severity | Findings | Of which fixed |
|---|---|---|
| wrong | 5 | 5 |
| overclaim | 6 | 6 |
| stale | 21 | 21 |
| unclear (incl. understatements) | 12 | 9 (#46 only on edited lines) |
| unsupported (could not be reproduced from a test) | 2 | 2, labelled |

## Not changed

* **The author's statement** (methodology.md, "In the author's words") is
  untouched. Checked against the evidence, it holds:
  * The start date matches the first commit (2026-09-30).
  * Every mechanism it lists exists: lockstep, formal, mutation, sigrok,
    FPGA, gate level, STA, physical verification, a second agent.
  * It makes no claim stronger than the record.
* **The 40 MHz and ±15/±18 ns sweeps** are now labelled, not removed. Turning
  them into tests would be a test change, outside this review.

## Recommendations (need a decision)

* **A. The first screen.** (Done 2026-10-30: the evidence table above "Where
  it stands".) The README's first screen says what the chip is
  and how it was made, then a long release bullet. A judge's main question
  is what is proven at which level, and that is the fifth bullet. Two
  options:
  * Move "Not yet on the Tiny Tapeout demo board or silicon" to the top of
    "Where it stands".
  * Or add a three-row evidence table above it: silicon none; FPGA (RTL in
    fabric) for most firmware; simulation and gate level for the rest, with
    gate level limited to the main suites.
* **B. methodology.md:37, "Claude Opus 5.5 and Astra".** Only the author can
  say whether "Astra" is intended or should read "Codex", which the table
  below it and review.md name as the reviewer.
* **C. Host-workflow docs.** Five docs read as internal run logs:
  usb-uart-byte, usb-uart-coexist, spi-packed-host, uart-capture-host and
  uart-rx-timing. Options:
  * Add a short summary at the top of each.
  * Or move the ticket logs (C13–C24, "Atlas", "settlement") to an
    appendix.
  * Say whether C24 was ever run.
* **D. Typography.** Missing spaces between words and digits recur in those
  docs. A regex fix could break identifiers (`uio0`, `F0`), so it needs a
  reviewed pass.
* **E. A new release receipt.** The two receipts certify `d916d40` and
  `f1cb7c9`, but `main` now ships different `ws2812` and `can_rx` firmware
  and the round-3 protocols. The netlist doesn't change, but any submission
  that claims "release-checked software" should either:
  * re-run `flow/release_evidence.py` on the shipping commit (about an hour
    on runner1);
  * or name `f1cb7c9` as the certified software.
* **F. Post-freeze protocols at gate level.** (Done later the same day:
  functional 16/16 and SDF typical 16/16 on fo6's netlist, as supplementary
  receipts outside the release gate; [release-evidence.md](release-evidence.md).)
  `test_post_freeze` (16 tests) had never run on a netlist. Adding it to a release receipt would let the
  README drop its "RTL only" caveat.
* **G. methodology.md, "What this caught".** The FPGA catching the assembler's
  truncating `/` (#42) is a good example of an independent check. It could
  be added there; this audit left that table alone.

## Could not check

* **Hardware outcomes.** KV260 transcripts, timings and Vivado reports need
  the board's logs (`pf*.json`, `hl.json`, runner1 reports). The docs'
  internal consistency was checked instead.
* **Physical numbers.** Slacks, utilization and hashes need fo6's run
  directory on runner1; they were checked for consistency across docs and
  tags only.
* **Recorded results.** The 34-of-34 mutant kills, "1,194 tests at 85991cd",
  "517 tests on that branch" and the per-run clock counts and wall times in
  the USB/UART logs would need the mutation runner or full suites, which
  are too heavy for this review.
* **`make -C fpga/kv260/sim` 5/5 with today's 246-word `usb_enum`.** That
  needs a cocotb run.
* **Simulation-only claims.** USB electrical figures, the eth10 receiver
  circuit values and RP2350 transport behaviour have no hardware evidence.
  The docs already say so.
* **The tests behind the docs' timing claims.** Doc wording was checked
  against the tests' assertions, but the tests were not re-run, except
  test_usb_uart_coexist.
