# Rules check (2026-11-03)

An adversarial read of the competition's own text against the entry as a judge
would see it, plus a refreshed survey of the public rivals. Base: branch
`improve-2026-11` at `6d5bdd5` (71 commits ahead of `main` `61af9b2`; `src/`
identical to `main`). Physical candidate: run fo6. Nothing here was run on the
Kria or any board; no file under `src/` or `flow/` was changed.

Evidence levels as used throughout the repository: model, RTL simulation,
formal, gate level (GL), GL with SDF, RTL in FPGA fabric, silicon. FPGA is not
silicon.

## 1. What the competition says

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
accept 8x4; Jane Street has not announced it. `tt-gds-action@ihp-cmos5l` installs
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
| IHP CMOS5L through TT, from the CMOS5L template | met | `info.yaml` yaml_version 6; `gds.yaml` uses `tt-gds-action@ihp-cmos5l` with `pdk: ihp-sg13cmos5l`; fo6 hardened locally with the action's PDK and LibreLane revisions | The official GDS action has **never run**, because Actions is **disabled** on the repository (`gh api repos/josusanmartin/wirelingo/actions/permissions`: `"enabled": false`; `actions/runs`: 0). Zero runs says nothing either way about whether it would pass. fo6 needs six substituting steps from `librelane_plugin_protoemu.py`, exercised only by local `flow/harden.sh` runs, which call the same `tt_tool.py --harden`. TT's FAQ: a failing GDS action blocks submission |
| tiles "6x4" in info.yaml | met | `info.yaml` `tiles: "6x4"`; 47 % cell / 53 % with macros (physical.md) | Three public rivals already harden at 8x4 (Loom, PRISM, JLNuijens), and TT's tools (`d66cf179`) and its new CMOS5L SRAM example's `info.yaml` accept it (the Verilog template's comment does not list it). If Jane Street allows it, they have 30 % more area; fo6 cannot change without a new signoff |
| Open source | **not yet** | `LICENSE` Apache-2.0 | The repository is **private** (`gh api`: `"private": true`, last push 2026-10-30). Rivals have been public since September-October. Publication is the author's call |
| Deadline 2027-01-18; final form | not yet | — | The form is not posted; somebody must watch the page and the sign-up email |
| Teams | n/a (advice) | one author with agents, disclosed in methodology.md | Not a rule |
| TT required fields | met | info.yaml title/author/description/language; info.md "How it works", "How to test", "External hardware" | `discord` blank (optional) |
| TT clock | met against SKY130 figures only | fo6 50 MHz at typ/slow/fast (STA, release gate); USB modes need exactly 48 MHz and `bringup.setup()` refuses any other actual frequency | TT's "At least 50MHz" (FAQ) and 66 MHz input rating (clock page) are SKY130 figures; TT publishes nothing for the IHP pads or clock input. 48 MHz from the RP2350 has not been tried on a demo board (model-tested only, info.md) |
| TT I/O speed | **at risk (claim)** | info.md: "SCK up to the project clock (about 50 Mbit/s); an optional quad mode moves 4 bits per SCK (about 200 Mbit/s)" | Stated as datasheet fact. Evidence is RTL simulation and the FPGA's own fabric SPI master. TT's GPIO page reports a 20 ns pad round trip on sky130 and nothing for IHP; a read with half a 20 ns period of margin is unlikely through real pads. isa.md:635-636 ("Board throughput and pad timing require separate measurements") and board-transport.md:75 ("Still to measure on a board") say so; the datasheet does not |
| SRAM for instruction memory | met, at risk | two `RM_IHPSG13_1P_256x32_c2_bm_bist` macros; LVS/DRC clean; SDF uses the macro's timing views | TT's memory page names only 1024x8 as taped out and tested, and the post's "examples of SRAM running on this process node" link is that same 1024x8 test. If the 256x32 macro or its integration fails on silicon, no firmware runs: there is no flop fallback in the tapeout build |
| Reprogrammable for new protocols after fabrication | met (strong) | 14 post-freeze protocols in 3 rounds, RTL, GL, SDF typ (slow/fast recorded on this branch), KV260; a blind test (HD44780, ATA PIO) at model and RTL level | The README does not link the blind test. Post-freeze needed one assembler fix (disclosed) |
| Not fixed protocol blocks | partly | cores + ISA; line unit does NRZI, stuffing, Manchester and clock recovery on core 0 | The line unit is protocol-specific hardware. explain-wirelingo.md Q6 admits "not fully pure". info.yaml's one-liner literally says "PIO-style" |
| "consider what you'd do differently" from PIO/PRU | partly | README "What we'd do differently from PIO"; pio-comparison.md (model) | pio-comparison.md says PIO is faster and smaller (model vs a PIO model; no RP2040 silicon). A judge may read the ISA as PIO plus extras |
| UART, SPI, I2C | met | RTL, GL (main suite), KV260 against our own models | No third-party device (real EEPROM, sensor, USB-UART adapter). fault-testing.md: `i2c_master` reads ACK from a stuck SDA; `uart_rx` takes a glitch for a start bit. Fixes are in verification (item 023), not on `main`, and the README rows don't mention them |
| Low-speed USB (stretch) | met at FPGA level | KV260 fabric against the Kria's Linux xHCI: 18/18 LS and 14/14 FS attaches, 58 + 130 resets, 10,000 + 56,000 descriptor reads; 350/350 data round trips at FS (transcripts in fpga/kv260/README.md) | FPGA pins, one host and hub, no scope, no raw logs in the repo; FS `SET_CONFIGURATION` stall 5/116 open; the Kria is now unresponsive and off limits, so nothing can be re-run |
| 10 Mbit Ethernet (stretch) | partly | TX: RTL, FPGA sampled; RX: RTL with 802.3 jitter, model sweeps; ARP/ping/UDP at the pads on model, RTL and fo6 GL; Linux pinging the model over TAP | No cable, no magnetics, no comparator: the RX front end is a proposed circuit (eth10-receiver.md). "Ping" is a Linux stack against the cycle model, not against the chip or its RTL in fabric |
| JTAG, SWD, PS/2, CAN | partly | JTAG, PS/2, CAN: model + KV260 against pre-timed models; SWD model only, parked | No real target, transceiver or keyboard |
| Test RTL on an FPGA | met | KV260 at 50 and 48 MHz | README:31 and info.md:46-48 overstate coverage (see §3, items 1 and 17) |
| "anything else your architecture makes possible" | met | logic analyzer + I2C clone/MITM, cut-through Ethernet match, PPS timestamps, bench firmware (MDIO, QSFP-style, PMBus) | Mostly model/RTL; eth10_match passed its verifier only on the fourth attempt |
| Verification techniques (formal, random, AI-assisted) | met (strong) | lockstep model vs RTL, SymbiYosys proofs, netlist-RTL equivalence (7429 points), mutation 34/34, sigrok oracles, independent verifier agent, Hardcaml port proved equal to `src/` | Same agents wrote spec, model, RTL and tests (stated). Mutation count recorded 2026-10-05, not rerun. Verifier reports are "not published with this repository". The CI `formal` and `mutants` jobs have never run on GitHub |
| Hardcaml (what Jane Street uses; not required) | partly | `ocaml/` port proved equal to `src/` (hardcaml-port.md) | The taped-out source is SystemVerilog; the port came after the freeze |
| Synthesis early, P&R, timing | met | fo6 signoff, release gate passes with no waiver; netlist reproduced bit for bit on a 4-CPU clean build | Fast-corner hold +0.095 ns; GitHub's standard runner for a **private** repo has 2 CPUs / 8 GB, for a public one 4 CPUs / 16 GB (https://docs.github.com/en/actions/reference/runners/github-hosted-runners); the 4-CPU local reproductions (ci6, ci7) took 1 h 46 min to 1 h 57 min |
| Explain the design (Advent of FPGA precedent) | at risk | methodology.md author statement; explain-wirelingo.md | methodology.md: "I let the agents lead" and "I cannot personally inspect thousands of gates". explain-wirelingo.md is a coaching page ("The goal is that you can stand in front of an engineer"; "AUTHOR TO ANSWER: How much of src/ have you read yourself?"). Published as is, it tells a judge the author may not be able to explain the chip |
| AI disclosure | met | methodology.md, "Who did what"; commit trailers (355 of 433 commits on this branch co-authored by Claude) | methodology.md:37 "Claude Opus 5.5 and Astra" is unexplained (open since claims audit B, 2026-10-14) |
| Licence hygiene | met | Apache-2.0; files copied from kdp1965/ihp-um-janestreet-prism say so in their headers (`src/pdn_cfg.tcl`, `flow/odb_stripes.py`, `flow/odb_sram_diodes.py`, `librelane_plugin_protoemu.py`) | The README never credits PRISM, a rival entry whose SRAM/PDN plumbing the flow reuses. A one-line credit costs nothing |

## 3. Claims a judge could catch

Each is checked against the repository at `6d5bdd5`. Severity as in
claims-audit.md: **wrong**, **overclaim**, **stale**, **unclear**, **unsupported**.

| # | Sev. | Where | Claim | What the repository shows |
|---|---|---|---|---|
| 1 | wrong | README:31 (evidence table) | FPGA against our own far ends: "Every firmware except SWD and the two-core USB pairs" | `fpga/kv260/README.md` never mentions `pps_capture`, `mdio_master`/`mdio_phy`, `eth10_tx_cs`, the QSFP/PMBus bench firmware, `rmii_*`, or the blind-test `hd44780_4bit`/`ata_pio`; the README's own rows list them as model/RTL only. The UART/USB pair modes are model and RTL only too |
| 2 | stale | verification-story.md:96-106 | `0bdcbe5`: "No report when this page was written"; "Until a PASS, treat the `eth10_match` numbers as unverified" | Verifier [report 009](../evidence/verifier/009-eth10-match-fix3.report.md) is `VERDICT: PASS` for `0bdcbe5` |
| 3 | unclear | README:94 vs verification-story.md:32 vs README:286 | README:94: post-freeze "with SDF at 20 ns 16/16 at the typical, slow and fast corners"; README:286: "With SDF at the typical corner all 16 pass"; verification-story: slow/fast "with no verifier report yet" | The slow/fast run has receipts (release-evidence.md:230) but sits outside the release gate and has no verifier report. Say the same thing in all three places |
| 4 | stale | methodology.md:144 (record, not the author's statement) | "Nothing formally proves that synthesis preserved the RTL's behaviour" | equivalence.md proves fo6's netlist equal to the RTL; README:288 says so. explain-wirelingo.md Appendix B found this and left it |
| 5 | stale | methodology.md:50 | "At commit 19ebe98, 164 of the repository's 184 commits are co-authored by Claude" | Dated, so not false; this branch has 355 of 433. Refresh at the freeze |
| 6 | overclaim | info.md:60-61 (datasheet) | host port "about 50 Mbit/s", quad "about 200 Mbit/s" | RTL simulation and the FPGA's fabric SPI master only; no RP2350, no pads (see §2, TT I/O) |
| 7 | unclear | README:37-40, verification-story.md:27 | "66,000 of 66,000 control transfers" | The KV260 doc's table says "10,000 of 10,000 right bytes, 0.66 ms each" and "56,000 of 56,000 right bytes": the unit reads as bytes. Say "descriptor reads" in both |
| 8 | unsupported (to a judge) | README:37-43, fpga/kv260/README.md | real-host USB counts | Transcripts pasted into a doc; no raw kernel logs or `usb_stress.py` output in the repository; the board is now unreachable |
| 9 | overclaim by omission | README rows `uart_rx`, `i2c_master`, `can_rx`, `usb_enum` | "RX tolerates a ±3 % sender ..."; "every SCL release times out" | fault-testing.md lists six real failures in these four firmwares (glitch start bit, ACK from stuck SDA, lost transfer after a truncated read, no CAN form check, idle-glitch error flag, missed reset inside a token). None is linked from the README. Fixes are under verification (item 023) |
| 10 | unclear | info.md:38-48 | "The firmware verified in simulation covers ... an SWD debug probe" | SWD is model only and parked; most others are RTL. Say the level per item or drop SWD from the datasheet list |
| 11 | unsupported (to a judge) | verification-story.md:93-94 | [Reports 000-023](../evidence/verifier/README.md) | "not published with this repository". A judge can't read the verifier's verdicts the page leans on. Publish them, or cite only what is in the repo |
| 12 | stale | release-evidence.md, README:44-53 | "certified software `7685aba`" | Honest, but `firmware/` has 11 files and 1,118 lines added since `7685aba`, none under a receipt. The freeze must rerun `flow/release_evidence.py` on the shipping commit |
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
4. **Credit PRISM in the README** (one line, with the files reused).
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
   1 h 57 min); a private repo would get 2 CPUs / 8 GB.
8. **Rerun the release gate on the shipping commit** after the firmware fixes
   (item 023) land: about an hour on runner1. Add `test_post_freeze` at SDF
   slow/fast to the gate so README:94 rests on a gated receipt.
9. **Publish the verifier reports, the release-evidence archive and the KV260
   USB logs** (author's decision), for example as GitHub release assets the way
   MarcosAsh does, or stop citing them as evidence a judge can check.
10. **Real third-party far ends**, when the author has hardware time: one real
    I2C sensor or EEPROM, one real USB-UART adapter, one real CAN transceiver,
    and the Ethernet receive front end on a cable. Each would move a protocol
    from "our own models" to "a device someone else built". The Kria is off
    limits until the author says otherwise.
11. **8x4 contingency:** decide in advance whether a third core would be used
    if 8x4 is announced, given that every receipt would have to be redone.

## 5. Rivals

Read 2026-11-03 through the GitHub API and the rivals' own files (READMEs and
the docs they point to, not only headlines). Everything is the rival's own
claim; private entries are invisible. A GitHub search for repositories created
since 2026-09-10 lists more than 150 candidates; most are templates or a
UART. The table keeps the ones with real content. "FPGA+3P" means an FPGA
build against a real third-party device or host; no repo in this table shows silicon.

| Repo | Last commit | Commits | Design | Highest evidence shown (their claim) | Real third-party far end? | CI gds / tiles | Notes |
|---|---|---|---|---|---|---|---|
| [parthDOOM/escapement](https://github.com/parthDOOM/escapement) | 2026-11-01 | ~94 | 4-context barrel core, deadline-checked pin events; 2x the same 256x32 macro as ours | GDS rated 33 MHz (signed off at 30 ns); netlist proved equal to RTL; unbounded proof that no pin edge is late; precheck | No. fpga/README.md: "The bitstreams are built and timed but not yet run on a board." Its USB/IP demo is a real Linux kernel against the *simulated* chip | green / 6x4 | The other 6x4 entry in this table with the same macro, gds green: layout-level evidence for the macro, not silicon |
| [MarcosAsh/protocol-emulator](https://github.com/MarcosAsh/protocol-emulator) | 2026-11-03 | ~1610 | 2 cores with a host-side timing-certificate checker, **Hardcaml**, 3x 512x16 SRAM | GDS slow +3.49 ns at 50 MHz, precheck; deadline proofs; netlist equal to RTL | **Yes.** Icepi Zero (ECP5) USB keyboard: real Linux `xhci_hcd` enumerates "HID 1209:0001 Keyboard", kernel log + sigrok capture in [release evidence-2026-10-15](https://github.com/MarcosAsh/protocol-emulator/releases/tag/evidence-2026-10-15); the chip's own host is a Pico running their MicroPython `demo_usb.py` (`run.txt:35`; `pico_b.log` is a second Pico listening on UART) | green / 6x4 (8x4 experiment branch) | Publishes CI and board evidence as GitHub releases. Written in Jane Street's own HDL |
| [mrnathanhumphrey-droid/protocol-emulator-asic](https://github.com/mrnathanhumphrey-droid/protocol-emulator-asic) | 2026-10-26 (`dev`; canary pushes 11-02) | ~360 | table-driven 5-lane EDF scheduler, 8 opcodes, flop store; Lean proofs | GDS 50 MHz all corners, slow +2.68 ns, precheck, GL green | **Yes.** Artix-7 against an MB85RC256V FRAM, a DS18B20 and a USB-UART adapter; I2C and 1-Wire runs captured by a DSLogic analyser; what is committed is decoded records (`fpga/witness/*.json`, "a RECORDED BENCH WITNESS ... not a hardware run in CI"), not raw captures | green / 6x4 | Its own README says the UART run is not a recorded witness |
| [jeddfadhley/protocol-emulator-asic](https://github.com/jeddfadhley/protocol-emulator-asic) (PEMU) | 2026-10-12 | ~23 | 2 PIO-like engines, packet RAM + DMA | GDS typ +2.72 ns at 50 MHz, slow about 36 MHz; Basys 3 on-chip loopback, 100/100 Ethernet frames | No. Headline "plug it into a laptop and ping it"; docs/fpga.md: "Not yet tried: Ethernet to a real laptop through an RJ45 jack" | green / 6x4 | Headline contradicted by its own doc; quiet for three weeks |
| [thomasgilbert481/tt_um_loom](https://github.com/thomasgilbert481/tt_um_loom) | 2026-11-03 | ~137 | 4-thread barrel core, per-thread bit engines, 512x16 SRAM | GDS 50 MHz typ, about 43 MHz slow; 109/109 GL | No: "Nothing has run on hardware" | green / **8x4** | Reports (second-hand) an organiser email of 2026-09-28: keep designing at 6x4; an IHP SRAM design can go on the shuttle |
| [kdp1965/ihp-um-janestreet-prism](https://github.com/kdp1965/ihp-um-janestreet-prism) | 2026-10-15 | ~103 | PRISM state-machine engine + TinyQV RISC-V, 2x 512x32 SRAM | GDS with a forked tt-support-tools; protocols in simulation and GL | No | green / **8x4** (custom) | Says 8x4 is "the size allowed by the Jane Street competition". **Our SRAM/PDN flow code is adapted from this repo** |
| [prasadp4009/tt_um_pemu](https://github.com/prasadp4009/tt_um_pemu) (Tarang) | 2026-10-30 | ~133 | timed ISA core + independent trace Observer | GDS, precheck; slow −2.82 ns (≤ 43.8 MHz) | No: Zybo runs against in-fabric pad models | green / 6x4 | |
| [theotacconi/openPIO](https://github.com/theotacconi/openPIO) | 2026-11-02 | ~199 | 2 PIO state machines + acceleration unit, 4 SRAMs | GDS 50 MHz typ, 39 MHz worst | Not shown (Tang Nano 20K build added 11-02, no recorded run) | green / 6x4 | Learns a protocol from a logic-analyser `.sr` recording (on the model) |
| [Vertrex-star/ProEm](https://github.com/Vertrex-star/ProEm-Open-Source-Protocol-Emulator) | 2026-11-01 | ~57 | 2-channel frame-chain engine | GDS slow +3.84 ns at 50 MHz | No: "Still to do: ... a test on real hardware" | green / 6x4 | |
| [jlsviper/fipe](https://github.com/jlsviper/fipe) | 2026-10-22 | ~46 | fault-injecting emulator, Hardcaml, flop memory | GDS slow +3.63 ns at 50 MHz, netlist equal to RTL | No (Icepi Zero "Next") | green / 6x4 | Chose flops because "an SRAM macro there would be unproven" |
| [JessicaTemplet/td-ut-protocol-emulator](https://github.com/JessicaTemplet/td-ut-protocol-emulator) | 2026-10-30 | ~39 | 2 microcode engines, Hardcaml, 512x8 SRAM | GDS, GL, precheck | No | green / 6x4 | |
| [TejasDasa/protocol-emulator-asic](https://github.com/TejasDasa/protocol-emulator-asic) | 2026-10-06 | ~98 | 5 state machines, CFGMEM macros | local signoff about 45 MHz slow; Cora Z7 | Operator-attested only: UART into a CP2102 adapter, no capture committed | no TT CI | |
| [fuzzylogique/weft](https://github.com/fuzzylogique/weft) | 2026-10-29 | ~8 | 16-bit CPU, flop IMEM | GDS 50 MHz every corner, precheck | No: "FPGA build: not attempted" | green (10-28) / 6x4 | README also says "No GDS has been produced yet" |
| [Abagel-coder/protocol-emulator](https://github.com/Abagel-coder/protocol-emulator) | 2026-10-18 | ~85 | time-triggered core, 512x16 SRAM | claims gds/precheck/GL jobs pass, slow +1.91 ns | No | **red** at workflow level / 6x4 | |
| [2AMLogic/sg13cmos5l-protocol-emulator](https://github.com/2AMLogic/sg13cmos5l-protocol-emulator) | 2026-11-02 | ~1032 | small ISA core, 256x16 SRAM | GDS at 2x2 | No | green / 2x2 | Spec "proposed"; no USB or Ethernet |
| [JLNuijens/ttihp-cpu-1-1-uart](https://github.com/JLNuijens/ttihp-cpu-1-1-uart) | 2026-10-25 | ~131 | counter fabric, 4x 512x32 SRAM | GDS at 8x4 | No | green / **8x4** | |
| [lblommesteyn/relwire](https://github.com/lblommesteyn/relwire) | 2026-10-09 | ~36 | relational protocol language, 4 cores | GDS slow +3.14 ns at 50 MHz | No | green (tt-relwire) / 6x4 | |
| Wirelingo (ours, private) | 2026-11-03 | 433 | 2 cores, 256-word SRAM banks, line unit; SystemVerilog + Hardcaml port | fo6 50 MHz all corners, release gate, netlist equal to RTL (local runs; no CI) | **Yes, one host:** KV260 fabric against the Kria's own Linux xHCI, LS and FS enumeration; all other far ends are our own models | never run / 6x4 | |

**Where a rival has shown on hardware what we have not:**

* **mrnathanhumphrey-droid** has I2C against a real FRAM and 1-Wire against a
  real DS18B20 on an FPGA, with records decoded from logic-analyser captures
  committed and re-checked in CI. Every one of our I2C, 1-Wire, SPI, CAN and UART runs on the KV260
  is against our own models or the other core.
* **MarcosAsh** has a real Linux host enumerating its FPGA build as a USB HID
  keyboard that types text, with the kernel log and capture published as a
  release. Ours is comparable in level (real Linux xHCI, LS and FS, FPGA
  fabric). Both runs have a microcontroller or CPU as the
  chip's host (theirs a Pico, ours the Kria's CPU over the SPI port). Two
  differences: their USB host is a separate PC, while ours is the Kria's own
  Linux on the board that holds the FPGA; and our evidence is a transcript in a
  doc, while theirs is a kernel log and capture a judge can download today.
* **Hardcaml:** MarcosAsh, fipe and TD-µT are written in Hardcaml. Ours is a
  port proved equal to the SystemVerilog after the freeze.
* **Formal timing guarantees:** Escapement (unbounded "never late" proof) and
  MarcosAsh (deadline certificates) prove timing properties for every
  accepted program. Ours checks deadlines with a static timing checker per
  firmware and does not prove them for all programs.
* **Public CI:** almost every serious rival has a green public gds and
  precheck run. Ours has none.

In the repos read, none shows silicon or a real Ethernet link; neither do we.
That describes public repos on 2026-11-03 and nothing more.

**Second-hand organiser statements** (reported by rivals, not checked):
Kanishk234 `docs/reports/COMPETITION_UPDATE_2026_10_09.md`: "SRAM macros,
including multiple macros, are allowed within area and Tiny Tapeout precheck"
and "Planned maximum 8x4 is an upgrade once the official CMOS5L template is
released". TT's own new CMOS5L SRAM example
([TinyTapeout/ttihp-cmos5l-sram-example](https://github.com/TinyTapeout/ttihp-cmos5l-sram-example),
created 2026-11-02, still the bare template) lists 8x4 among valid `tiles`
values. The TT staging shuttle
[ttcodebot/tinytapeout-ihp-27a-staging](https://github.com/ttcodebot/tinytapeout-ihp-27a-staging)
is `ttihp27a` on `ihp-sg13cmos5l` with a placeholder `end_date: '2026-12-31'`;
no competition entries are listed. Nothing found says the 256x32 macro is, or
is not, proven in silicon. The author could ask asic-competition@janestreet.com
about 8x4 and macros; agents may not.
