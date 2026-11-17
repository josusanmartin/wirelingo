# Rules check (2026-11-03)

An adversarial read of the competition's own text against the entry as a judge
would see it. Base: branch
`improve-2026-11` at `ac4e8e7` (71 commits ahead of `main` `af2c097`; `src/`
identical to `main`). Physical candidate: run fo6. Nothing here was run on the
Kria or any board; no file under `src/` or `flow/` was changed.

Evidence levels as used throughout the repository: model, RTL simulation,
formal, gate level (GL), GL with SDF, RTL in FPGA fabric, silicon. FPGA is not
silicon.

## Current scope of this historical audit

The tables below retain the 2026-11-03 snapshot; they are not a current
submission checklist. **The tile budget is now 8x4:** the competition's
November 9 update confirms 8x4 tiles for the March 2027 IHP shuttle. Its
FAQ requires a public repository with RTL, tests and a
passing Tiny Tapeout GDS/precheck, plus architecture/ISA documentation,
tooling/examples and a verification write-up. AI use is allowed with a
write-up describing it. The deadline remains January 18, 2027.
These are paraphrases of the [official rules and FAQ](https://blog.janestreet.com/protocol-emulator-asic-competition/),
checked during the Astra review. The FAQ specifies no target clock or fixed
protocol speed; the selected clock must close timing and support the claimed
protocols. The entry's clock is 48 MHz: the 3-core 8x4 release build
meets it in STA at every corner and misses 50 MHz at the slow corner. See
[summary.md](summary.md) for the release build and
[physical.md](physical.md#the-3-core-release-candidate-2026-11-15) for its
physical results. The older statements below about a missing FAQ, an
unannounced 8x4 tile and 6x4 limits are superseded. In the rule table (§2)
the rows whose status has changed since the snapshot (the GDS action, the
tile, open source, licence credit, and on 2026-11-17 the clock, I/O speed,
SRAM, FPGA, verification, Hardcaml, synthesis, explanation and AI-disclosure
rows) say so, with the 2026-11-03 state kept as history.

## 1. What the competition said at the audit date

Everything below was fetched on 2026-11-03. The competition page has no judging
rubric, no FAQ, no eligibility clause, no AI-use rule and no submission format
beyond "Submit your design by January 18th, 2027." The submission form is not
up yet. Quotes below are verbatim; paraphrase is marked as such.

**Competition post**, https://blog.janestreet.com/protocol-emulator-asic-competition/
(Sep 10, 2026; Benjamin Devlin, Anish Singhani):

* Selection: "This time, you're designing the chip, and we'll pay to fabricate
  our favorite designs!", "We're particularly interested in projects with unique
  functionality, as well as those that demonstrate novel approaches to design
  and verification methodologies!" and "We'll pay to tape out the most novel
  designs on a Tiny Tapeout shuttle." Selection is the organisers' taste
  ("favorite"), not novelty alone.
* The task: "Design an open-source, general-purpose protocol emulator ASIC."
* "The goal isn't to put a UART block, an SPI block, and an I2C block on one die
  and call it done. Your chip should be reprogrammable enough to support new
  protocols after fabrication, within its timing and I/O constraints. For
  inspiration, look at the PIO state machines on the RP2040 or the PRU cores on
  TI's Sitara parts, and consider what you'd do differently."
* "Start with UART, SPI, and I2C." / "Stretch goals include low-speed USB and
  10Mbit Ethernet." / "Other interesting protocols to consider: JTAG, SWD, PS/2,
  CAN bus"
* "If you have access to an FPGA, consider using it to test your RTL before the
  ASIC flow."
* "Show us anything else your architecture makes possible that we haven't
  thought of."
* "We are excited to see the languages and verification techniques you use,
  including formal methods, random constrained tests, AI-assisted verification,
  and more. As AI-assisted chip design becomes more common, we believe
  verification will be an extremely important aspect of the ASIC design flow
  going forwards."
* Process: "We're targeting IHP's 130nm CMOS5L process through our friends at
  Tiny Tapeout. Start with the CMOS5L Verilog template, which takes you from RTL
  to GDS. Set the tile size in info.yaml to 6x4."
* Area: "The current maximum area is 6x4 tiles per design. We are working on
  the possibility of scaling up to 8x4 tiles (~30% more area). We'll update this
  page, as well as emailing everyone who has signed up if the larger tile size
  becomes available."
* Open source: "Your submission should be open source so others can use and
  build on it. ... feel free to build in public!"
* Teams: "we strongly recommend working in teams."
* Deadline: "Submit your design by January 18th, 2027."
* Prize: "We're targeting the March 2027 CMOS5L shuttle, subject to the foundry
  schedule. Winners will receive chips and dev boards back after fabrication."
* Fit: "budget for about 1K logic cells per tile." "For instruction memory, SRAM
  can be more area-efficient than flip-flops. Tiny Tapeout has examples of SRAM
  running on this process node you can reference." (The link is the ttihp0p2
  `tt_um_urish_sram_test`, the 1024x8 macro, not the 256x32 we use.) "Run
  synthesis early ... Then run the full place-and-route flow and check timing."
* "We'll add a final submission form to this page closer to the deadline!"

**CMOS5L template**, https://github.com/TinyTapeout/ttihp-verilog-template/tree/cmos5l
(last commit 2026-10-19, `231f26a5` "fix(test): add sg13cmos5l UDP models to
gate-level simulation", after "chore: switch from ihp-sg13g2 to ihp-sg13cmos5l"
the same day):
`info.yaml` "Do not change! yaml_version: 6"; "Your top module name must start
with "tt_um_""; "Source files must be in ./src and you must list each source
file separately"; pinout "DO NOT delete or add any pins"; `docs/info.md`: "Each
image must be less than 512 kb in size". Default licence Apache-2.0. Workflows:
`gds` (build, precheck, gl_test, viewer), `docs`, `test`, `fpga`.

**Tiny Tapeout FAQ**, https://tinytapeout.com/faq/: "A project can't be
submitted to a shuttle if its GDS action is failing". For the Documentation
action the FAQ lists the fields it requires (paraphrase of a bullet list):
author, title, description, how_it_works, how_to_test, language. Its "At least
50MHz" sits under the chip specs for TT04 to TT10, the SKY130 shuttles; the FAQ
gives no IHP figure.

**TT specs.** https://tinytapeout.com/specs/clock/: "maximum input frequency of
66 MHz", the rating of the SKY130 `sky130_ef_io_gpiov2_pad`; nothing
IHP-specific. https://tinytapeout.com/specs/gpio/: "For SKY130 the official rating is
33 MHz"; "the worst round trip latency was measured to be 20 ns" (TT3.5
silicon); nothing IHP-specific. https://tinytapeout.com/specs/memory/: "IHP has
several variations of SRAM macros which can be used in shuttles which they will
manufacture"; "One of these macros, 1024x8, has been successfully taped out and
tested to be working"; "Integrating the IHP SRAM macro at this stage is not
trivial."

**Tools.** `TinyTapeout/tt-support-tools` branch `ihp-sg13cmos5l`, commit
`d66cf179` (2026-09-21): "feat(ihp): 8x4 tile size for ihp-sg13cmos5l". The tools
accept 8x4; Jane Street had not announced it at the audit date (it has since:
see the scope note above). `tt-gds-action@ihp-cmos5l` installs
LibreLane `3.1.0.dev3` and IHP-Open-PDK `2bbec755`; runner1's `~/ttpdk` (fo6's
PDK) is the same revision.

**Precedent, not a rule here.** Jane Street's Advent of FPGA,
https://blog.janestreet.com/advent-of-fpga-challenge-2025/: "Original work only:
no duplicates or obviously AI-generated submissions. You should be able to
explain your design." The protocol-emulator post has no such clause and asks
for "AI-assisted verification", but the same team may judge.

## 2. Rule and criterion table

Status: **met**, **partly**, **not yet** (required later), **at risk**.

| Rule or criterion | Status | Evidence in the entry | Risk a skeptical judge would raise |
|---|---|---|---|
| IHP CMOS5L through TT, from the CMOS5L template | met (locally, and on GitHub for the 3-core build) | `info.yaml` yaml_version 6; `gds.yaml` building for `ihp-sg13cmos5l` in three chained jobs (`gds_place`, `gds_route`, `gds`, since one job can overrun GitHub's 6 h job limit: [release-evidence.md](release-evidence.md#the-3-core-8x4-release-candidate)), now pinning `tt-gds-action` (`3412659`) and tt-support-tools (`d66cf17`), and comparing the build's netlist and dates-zeroed GDS hashes with [evidence/8x4-release-expected.json](../evidence/8x4-release-expected.json); the 3-core candidate hardened locally with the action's PDK and LibreLane revisions ([release-evidence.md](release-evidence.md#the-3-core-8x4-release-candidate)) | Now: Actions is enabled on the public repository, and GitHub has built the 3-core 8x4 design green with the hash step matching the release identity, with `precheck` and `gl_test` green: `a84401f` and `S` = `2c97c15` on a test branch, `bc8882b` on `main` (its third attempt; two were cancelled at GitHub's 6 h job limit, which is why the build is split) and `00cd1c6`, the three-job workflow, on the test branch ([evidence/8x4-release-20261116/](../evidence/8x4-release-20261116/README.md)). On `main` at `00cd1c6` (2026-11-17) the three build jobs, the hash step (`(match)`), `viewer`, `gl_test` and the `test` run are green; its `precheck` had not finished when this was written. The precheck job took 268 to 330 min of its 6 h limit on those runs, a measured risk ([release-evidence.md](release-evidence.md#the-3-core-8x4-release-candidate)). At the snapshot (2026-11-03) Actions was disabled and the action had never run; fo6 was the 6x4 candidate. The flow needs substituting steps from `librelane_plugin_protoemu.py`, exercised by local `flow/harden.sh` runs, which call the same `tt_tool.py --harden`, and by GitHub's builds above. TT's FAQ: a failing GDS action blocks submission |
| Tile size in info.yaml | met (now 8x4) | `info.yaml` `tiles: "8x4"`; the 3-core candidate at 57.0 % cell / 62.3 % with macros ([physical.md](physical.md#the-3-core-release-candidate-2026-11-15)) | Superseded: the competition confirmed 8x4 on 2026-11-09. At the snapshot `info.yaml` said 6x4 (fo6: 47 % cell / 53 % with macros), 8x4 was unannounced, and TT's tools (`d66cf179`) already accepted it |
| Open source | met (now) | `LICENSE` Apache-2.0; the repository is public (josusanmartin/wirelingo) | At the snapshot the repository was private (`gh api`: `"private": true`, last push 2026-10-30) |
| Deadline 2027-01-18; final form | not yet | — | The form is not posted; somebody must watch the page and the sign-up email |
| Teams | n/a (advice) | one author with agents, disclosed in methodology.md | Not a rule |
| TT required fields | met | info.yaml title/author/description/language; info.md "How it works", "How to test", "External hardware" | `discord` blank (optional) |
| TT clock | met at 48 MHz (STA); against SKY130 figures only | Now: the release build (shipping commit `2c97c15`) closes 48 MHz (20.832 ns) at typ, slow and fast, worst non-latch setup slack +0.4304 ns at slow, and misses 50 MHz at the slow corner by 0.402 ns (−0.4016 ns at 20 ns) ([records](../evidence/8x4-release-20261116/README.md)). At the snapshot: fo6 50 MHz at typ/slow/fast (STA, release gate). USB modes need exactly 48 MHz and `bringup.setup()` refuses any other actual frequency | TT's "At least 50MHz" (FAQ) and 66 MHz input rating (clock page) are SKY130 figures; TT publishes nothing for the IHP pads or clock input. 48 MHz from the RP2350 has not been tried on a demo board (model-tested only, info.md) |
| TT I/O speed | at risk (now stated as unmeasured) | Now: info.md says "SCK up to the project clock: 1 bit per SCK (48 Mbit/s at 48 MHz), or in an optional quad mode 4 bits per SCK (192 Mbit/s on the wire at 48 MHz)", that the rates were shown in RTL simulation and in FPGA fabric at 50 MHz, that through the pads and the demo board they are unmeasured, and the pad-delay budget STA allows. At the snapshot it said "SCK up to the project clock (about 50 Mbit/s); an optional quad mode moves 4 bits per SCK (about 200 Mbit/s)" | At the snapshot this was stated as datasheet fact. Evidence is RTL simulation and the FPGA's own fabric SPI master. TT's GPIO page reports a 20 ns pad round trip on sky130 and nothing for IHP; a read with half a 20 ns period of margin is unlikely through real pads. isa.md:635-636 ("Board throughput and pad timing require separate measurements") and board-transport.md:75 ("Still to measure on a board") say so; the datasheet does not |
| SRAM for instruction memory | met, at risk | three `RM_IHPSG13_1P_256x32_c2_bm_bist` macros in the release build (two in fo6); LVS/DRC clean; SDF uses the macro's timing views | TT's memory page names only 1024x8 as taped out and tested, and the post's "examples of SRAM running on this process node" link is that same 1024x8 test. If the 256x32 macro or its integration fails on silicon, no firmware runs: there is no flop fallback in the submitted build |
| Reprogrammable for new protocols after fabrication | met (strong) | 14 post-freeze protocols in 3 rounds, RTL, GL, SDF typ (slow/fast recorded on this branch), KV260; a blind test (HD44780, ATA PIO) at model and RTL level | The README does not link the blind test. Post-freeze needed one assembler fix (disclosed) |
| Not fixed protocol blocks | partly | cores + ISA; line unit does NRZI, stuffing, Manchester and clock recovery on core 0 | The line unit is fixed hardware: a menu of line codings, stuffing rules and frame endings built with USB and 10BASE-T in mind (CAN-style stuffing also fits). The host picks from it with three register writes at load; software cannot add a coding (explain-wirelingo.md, "If protocols are firmware, why is there a line unit?"; line-unit-settings.md). info.yaml's one-liner literally says "PIO-style" |
| "consider what you'd do differently" from PIO/PRU | partly | README "What we'd do differently from PIO"; pio-comparison.md (model) | pio-comparison.md says PIO is faster and smaller (model vs a PIO model; no RP2040 silicon). A judge may read the ISA as PIO plus extras |
| UART, SPI, I2C | met | RTL, GL (main suite), KV260 against our own models | No third-party device (real EEPROM, sensor, USB-UART adapter). fault-testing.md: `i2c_master` reads ACK from a stuck SDA; `uart_rx` takes a glitch for a start bit. Fixes are in verification (item 023), not on `main`, and the README rows don't mention them |
| Low-speed USB (stretch) | met at FPGA level | KV260 fabric against the Kria's Linux xHCI: 18/18 LS and 14/14 FS attaches, 58 + 130 resets, 10,000 + 56,000 descriptor reads; 350/350 data round trips at FS (transcripts in fpga/kv260/README.md) | FPGA pins, one host and hub, no scope, no raw logs in the repo; FS `SET_CONFIGURATION` stall 5/116 open; the Kria is now unresponsive and off limits, so nothing can be re-run |
| 10 Mbit Ethernet (stretch) | partly | TX: RTL, FPGA sampled; RX: RTL with 802.3 jitter, model sweeps; ARP/ping/UDP at the pads on model, RTL and fo6 GL; Linux pinging the model over TAP | No cable, no magnetics, no comparator: the RX front end is a proposed circuit (eth10-receiver.md). "Ping" is a Linux stack against the cycle model, not against the chip or its RTL in fabric |
| JTAG, SWD, PS/2, CAN | partly | JTAG, PS/2, CAN: model + KV260 against pre-timed models; SWD model only, parked | No real target, transceiver or keyboard |
| Test RTL on an FPGA | met for the v0.2 RTL | KV260 at 50 and 48 MHz, on the v0.2 two-core RTL; the 3-core v0.3 RTL that ships has one board smoke run (2026-11-10), made before the CRC retime of 2026-11-12 | README:31 and info.md:46-48 overstate coverage (see §3, items 1 and 17) |
| "anything else your architecture makes possible" | met | logic analyzer + I2C clone/MITM, cut-through Ethernet match, PPS timestamps, bench firmware (MDIO, QSFP-style, PMBus) | Mostly model/RTL; eth10_match passed its verifier only on the fourth attempt |
| Verification techniques (formal, random, AI-assisted) | met (strong) | lockstep model vs RTL, SymbiYosys proofs, netlist-RTL equivalence (10754 points on the release netlist; 7429 on fo6), mutation 34/34 (2026-10-05) and 367/367 on GitHub, sigrok oracles, independent verifier agent, Hardcaml port proved equal to `src/` | Same agents wrote spec, model, RTL and tests (stated). Now: GitHub's `formal` and `mutants` jobs ran green at `2c97c15`, `bc8882b` and `00cd1c6`, `mutants` killing 367 of 367 ([records](../evidence/8x4-release-20261116/README.md)); the verifier's items and reports 000 to 078b are published in [evidence/verifier/](../evidence/verifier/README.md), later ones are not. At the snapshot the mutation count was the 2026-10-05 record, the reports were not published, and the CI jobs had never run on GitHub |
| Hardcaml (what Jane Street uses; not required) | partly | `ocaml/` port proved equal to `src/` (hardcaml-port.md) | The submitted source is SystemVerilog; the port came after the freeze |
| Synthesis early, P&R, timing | met | Now: the release build's release check passed with no waiver (`2c97c15`, 2026-11-16); its netlist and dates-zeroed GDS reproduced bit for bit by clean hardens on runner1 and by GitHub's builds ([records](../evidence/8x4-release-20261116/README.md)); hold at least +0.105 ns. At the snapshot: fo6 signoff, release gate passes with no waiver; netlist reproduced bit for bit on a 4-CPU clean build | Now: GitHub's GDS build takes 4 h 22 min to more than 6 h in one job, so it is split into three jobs, and Tiny Tapeout's precheck took 268 to 330 min of its 6 h limit (a measured risk, [release-evidence.md](release-evidence.md#the-3-core-8x4-release-candidate)). At the snapshot: fast-corner hold +0.095 ns (fo6); GitHub's standard runner for a **private** repo has 2 CPUs / 8 GB, for a public one 4 CPUs / 16 GB (https://docs.github.com/en/actions/reference/runners/github-hosted-runners); the 4-CPU local reproductions (ci6, ci7) took 1 h 46 min to 1 h 57 min |
| Explain the design (Advent of FPGA precedent) | at risk | methodology.md author statement; explain-wirelingo.md | methodology.md: "I let the agents lead" and "I cannot personally inspect thousands of gates". Now: explain-wirelingo.md is a walkthrough of the design for a reader; the coaching sentences quoted here at the snapshot ("The goal is that you can stand in front of an engineer"; "AUTHOR TO ANSWER: How much of src/ have you read yourself?") are no longer in it. At the snapshot, published as it was, it told a judge the author might not be able to explain the chip |
| AI disclosure | met | methodology.md, "Who did what"; commit trailers (at `001fafa`, 668 of 757 commits carry a Claude `Co-Authored-By` trailer and 11 a Codex model's; at the snapshot 355 of 433 on that branch carried Claude's) | Now: methodology.md says what "Claude Opus 5.5 and Astra" referred to as far as the records show, and names the Codex team, the Fable 5.2 commits and the November Codex commits. At the snapshot that sentence was unexplained (open since claims audit B, 2026-10-14) |
| Licence hygiene | met | Apache-2.0; the SRAM/PDN files copied from another Apache-2.0 repository say so in their headers (`src/pdn_cfg.tcl`, `flow/odb_stripes.py`, `flow/odb_sram_diodes.py`, `librelane_plugin_protoemu.py`) | At the snapshot the README did not credit that source; it now does (README, the physical-design notes) |

## 3. Claims a judge could catch

Each is checked against the repository at `ac4e8e7`. Severity as in
claims-audit.md: **wrong**, **overclaim**, **stale**, **unclear**, **unsupported**.

| # | Sev. | Where | Claim | What the repository shows |
|---|---|---|---|---|
| 1 | wrong | README:31 (evidence table) | FPGA against our own far ends: "Every firmware except SWD and the two-core USB pairs" | `fpga/kv260/README.md` never mentions `pps_capture`, `mdio_master`/`mdio_phy`, `eth10_tx_cs`, the QSFP/PMBus bench firmware, `rmii_*`, or the blind-test `hd44780_4bit`/`ata_pio`; the README's own rows list them as model/RTL only. The UART/USB pair modes are model and RTL only too |
| 2 | stale | verification-story.md:96-106 | `86abad7`: "No report when this page was written"; "Until a PASS, treat the `eth10_match` numbers as unverified" | Verifier [report 009](../evidence/verifier/009-eth10-match-fix3.report.md) is `VERDICT: PASS` for `86abad7` |
| 3 | unclear | README:94 vs verification-story.md:32 vs README:286 | README:94: post-freeze "with SDF at 20 ns 16/16 at the typical, slow and fast corners"; README:286: "With SDF at the typical corner all 16 pass"; verification-story: slow/fast "with no verifier report yet" | The slow/fast run has receipts (release-evidence.md:230) but sits outside the release gate and has no verifier report. Say the same thing in all three places |
| 4 | stale | methodology.md:144 (record, not the author's statement) | "Nothing formally proves that synthesis preserved the RTL's behaviour" | equivalence.md proves fo6's netlist equal to the RTL; README:288 says so. explain-wirelingo.md Appendix B found this and left it |
| 5 | stale | methodology.md:50 | "At commit 19ebe98, 164 of the repository's 184 commits are co-authored by Claude" | Dated, so not false; this branch has 355 of 433. Refresh at the freeze |
| 6 | overclaim | info.md:60-61 (datasheet) | host port "about 50 Mbit/s", quad "about 200 Mbit/s" | RTL simulation and the FPGA's fabric SPI master only; no RP2350, no pads (see §2, TT I/O) |
| 7 | unclear | README:37-40, verification-story.md:27 | "66,000 of 66,000 control transfers" | The KV260 doc's table says "10,000 of 10,000 right bytes, 0.66 ms each" and "56,000 of 56,000 right bytes": the unit reads as bytes. Say "descriptor reads" in both |
| 8 | unsupported (to a judge) | README:37-43, fpga/kv260/README.md | real-host USB counts | Transcripts pasted into a doc; no raw kernel logs or `usb_stress.py` output in the repository; the board is now unreachable |
| 9 | overclaim by omission | README rows `uart_rx`, `i2c_master`, `can_rx`, `usb_enum` | "RX tolerates a ±3 % sender ..."; "every SCL release times out" | fault-testing.md lists six real failures in these four firmwares (glitch start bit, ACK from stuck SDA, lost transfer after a truncated read, no CAN form check, idle-glitch error flag, missed reset inside a token). None is linked from the README. Fixes are under verification (item 023) |
| 10 | unclear | info.md:38-48 | "The firmware verified in simulation covers ... an SWD debug probe" | SWD is model only and parked; most others are RTL. Say the level per item or drop SWD from the datasheet list |
| 11 | unsupported (to a judge) | verification-story.md:93-94 | [Reports 000-023](../evidence/verifier/README.md) | "not published with this repository". A judge can't read the verifier's verdicts the page leans on. Publish them, or cite only what is in the repo |
| 12 | stale | release-evidence.md, README:44-53 | "certified software `5457d8e`" | Honest, but `firmware/` has 11 files and 1,118 lines added since `5457d8e`, none under a receipt. The freeze must rerun `flow/release_evidence.py` on the shipping commit |
| 13 | stale | claims-audit.md recommendation F | "`test_post_freeze` (16 tests) has never run on a netlist" | It has (release-evidence.md:216). The audit is dated 2026-10-14; mark it historical |
| 14 | unclear | usb-uart-byte.md, team-integration-checklist.md, lean-verification*.md | ticket jargon ("Atlas", C13-C24, "settlement") | claims-audit recommendation C, still open |
| 15 | unclear | methodology.md:37 | "Claude Opus 5.5 and Astra" | Unexplained; Codex is the named reviewer. Author's decision |
| 16 | unclear | whole repo | 43 docs, 15,275 lines; README first screen full of run names (fo6, t48ba, v2c, g4) and hashes | A judge with an hour will not find the strongest results. guide.md is the map but sits at README:139 |
| 17 | overclaim | info.md:46-48 (datasheet) | "Most of it (not SWD or the two-core USB device) has also run on an FPGA build of the RTL" | The same overclaim as item 1, where a judge reads it first: `pps_capture`, `mdio_*`, `eth10_tx_cs`, `rmii_*`, the bench firmware and the blind-test firmware appear nowhere in `fpga/kv260/README.md`, which also lists the USB-UART pairs as "Not run" |

No first/only/fastest claim was found in README.md, info.md, methodology.md or
verification-story.md.

## 4. Fixes, ranked (cheapest high-value first)

1. **Docs only, minutes each:** fix §3 items 1, 2, 3, 4, 7, 10, 13 and 17. All are
   wording against facts already in the repo.
2. **Link the weaknesses from the README.** One line under the firmware table
   pointing at fault-testing.md's six findings, and one at blind-test.md. Showing
   your own failures is the methodology's selling point; hiding them by omission
   is the overclaim a judge remembers. When the fault fixes (item 023) merge,
   those README rows must say the fixes are verified on the model and in RTL
   simulation only, not at gate level, on the FPGA or in silicon.
3. **Qualify the datasheet's host-port speeds** (info.md:60-61): "in RTL
   simulation and on the FPGA; through the TT pads unmeasured; the default
   demo-board driver (`tools/micropython/protoemu.py`) bit-bangs the port in
   MicroPython, far below either figure; the optional `quad_spi.py` PIO/DMA
   transport it points to is unmeasured on a board."
4. **Credit the source of the reused SRAM/PDN files in the README** (one
   line, with the files reused). (Since done.)
5. **A judge's one page.** Five lines at the top of README: what it is, the
   evidence table, the three results to look at first (post-freeze + blind
   test, real Linux USB host on FPGA fabric, netlist proved equal to RTL), and
   what is not shown. Move run names and hashes below.
6. **Decide what explain-wirelingo.md is.** Keep it out of the public tree, or
   rewrite it as a reader's walkthrough without the coaching and the `AUTHOR TO
   ANSWER` prompts (author's decision).
7. **Make the repository public and enable GitHub Actions** (author's
   decision, in the repository settings; agents may not push or change
   settings). Publishing alone runs nothing: Actions is disabled
   (`actions/permissions`: `"enabled": false`), so both steps are needed. This
   is what turns "the GDS action passes" from a local claim into
   TT's own evidence, and the open-source rule requires publication by the
   deadline anyway. Compare the action's netlist hash with fo6's `a94a948b…`;
   a mismatch must be known before January, not after. On a public repo the
   runner is 4 CPUs / 16 GB, the budget the local reproductions used (1 h 46 to
   1 h 57 min); a private repo would get 2 CPUs / 8 GB. (Since done: the
   repository is public with Actions enabled; for the 3-core build the gds
   job compares the netlist and the dates-zeroed GDS with the candidate's,
   and the 4-CPU local harden took 240 min.)
8. **Rerun the release gate on the shipping commit** after the firmware fixes
   (item 023) land: about an hour on runner1. Add `test_post_freeze` at SDF
   slow/fast to the gate so README:94 rests on a gated receipt.
9. **Publish the verifier reports, the release-evidence archive and the KV260
   USB logs** (author's decision), for example as GitHub release assets, or
   stop citing them as evidence a judge can check.
10. **Real third-party far ends**, when the author has hardware time: one real
    I2C sensor or EEPROM, one real USB-UART adapter, one real CAN transceiver,
    and the Ethernet receive front end on a cable. Each would move a protocol
    from "our own models" to "a device someone else built". The Kria is off
    limits until the author says otherwise.
11. **8x4 contingency:** decide in advance whether a third core would be used
    if 8x4 is announced, given that every receipt would have to be redone.
    (Since done: 8x4 was confirmed and the author chose three cores; see the
    scope note above.)

