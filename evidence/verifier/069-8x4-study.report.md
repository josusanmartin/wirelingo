VERDICT: PASS

069 8x4-study (branch `study-8x4`, tip `65d0e8e`, backlog AN): verifier report.
Kria: verifier does not touch it. No SWD.

The study's measurements reproduce.
- **Harden.** My own baseline harden from the tip gives the builder's netlist bit for bit, and the same metrics at every corner.
- **Synthesis.** My full synthesis rerun reproduces `results.txt` byte for byte.
- **Other figures.** Every routability and timing figure I checked matches its log on runner1. So do the STA reproduction of fo6, the SRAM liberty and LEF figures, and the layout claim.
- **The branch.** It is clean. It adds only new files on the public base and touches no `src/`, `flow/`, `firmware/` or `docs/methodology.md`. Privacy checks are clean.

The doc has wrong numbers to fix before it is published (findings 1–3, mostly attributions to the record). The argument about the second line unit's clock is half right (finding 4), and the plan's fallback leaves runner time unbudgeted (finding 5). None of these changes what the measurements say. The recommendation is a defensible option for the author (finding 5).

## Findings

1. **"65 clock nets" counts warnings, not nets. The doc also leaves out that plain grt3 already dropped clock NDRs.**
   - `docs/8x4-study.md:274-275` says the router "dropped the non-default rules of 65 clock nets (base and fo6: none)".
   - pkg3's `exp.log` has 66 `GRT-0273` warnings, repeated across the global-routing and post-GRT repair steps. Only about 19 distinct nets are named, 7 of them at the global-routing step.
   - The claim is used as a sign of congestion. But `grt3` (3 cores, no additions) dropped NDRs on 6 nets, and `grt3pkg` on 14. The doc says this nowhere.
   - Base has 0, as claimed.
   - Fix: "dropped the NDRs of about 19 clock nets (66 warnings over the post-GRT steps); grt3 already dropped 6".
   - Trivial: `grt4f2d58`'s "1001 extra iterations" counts 1000 in its log.

2. **Several figures attributed to the record are wrong.** I confirmed each against the file.
   - **The +1.5 % core wire for CRC-32** (`:427` and the ranking table `:491`, "v3 core_route") is not a v3 study measurement. `v3-study.md:10` says the v3 study ran no `core_route.sh`. Its row at `:56` says "+1.5 % core wire measured in physical.md", and the source is `docs/physical.md:455`.
   - **"v3's CRC-32: +2.2K µm² per core"** (`:419`). v3-study.md reports +2.06K (`:228`), "+2.1K" in its table. 2.2K is this study's own synthesis (results.txt row `2 V3_CRC32`, 4,394 over 2 cores). Label it as this study's figure.
   - **"synthesis noise … about ±0.5K µm² (the v3 study measured the same)"** (`:181-182`). v3-study.md:45 says ±0.4K.
   - **"about a hundred changed lines in `tools/pe/`"** on the v3-study branch (`:399-400`). The branch's `tools/pe` diff from its base is 9 files, +152 −37.
   - **The SPI device clock's crossings "met with +0.28 to +0.52 ns at 400 MHz"** (`:638`). `fpga/kv260/README.md:178-181` gives +0.276, +0.474 and +0.196 ns for the crossings. The +0.520 ns is the 400 MHz domain itself. The design WNS is +0.196 ns, on a crossing, so the range is +0.196 to +0.474 ns.
   - **"27 files mention 6x4 or two cores today"** (`:590`). `git grep -l -i -E "6x4|two cores"`, leaving out the study's own files, gives 41 tracked files: 23 in docs/, README and info.yaml, 38 outside evidence/. State the count with the grep behind it.
   - **"the formal Lean testbenches fixed at `NCORES(2)` (`formal/lean/*_tb.sv`)"** (`:292-293`). 2 of the 4 set `NCORES(2)` (`rtl_fetch_ownership_tb.sv`, `rtl_rx_autopush_tb.sv`). The other two instantiate `pe_core` alone. Name the two.

3. **Record item numbers, loosely cited.**
   - `:387-388` and `:530`: item 1 is "No `jmp !t`", a branch condition, not a compare. Items 2 and 25 are the compares.
   - Both lookup examples (LIN PID parity, DShot GCR) come from item 4. Item 15 is MIDI's data-byte count per status.
   - `:469`: "length-prefixed frames" for item 5 is the study's own generalisation; the item names Wiegand only.
   - All the other item numbers check: 3, 11, 13, 14, 16–19 and 21–23.

4. **The second line unit's clock argument is right for full speed, but overstated for low speed.**
   - **Full speed is right.** The receiver's period is a whole number of clocks (`docs/isa.md:272`, LINEDIV `[31:16]`, at least 4). USB FS needs f/12 MHz to be an integer, and 10BASE-T needs f/10 MHz. 60 MHz (5 and 6 clocks) is the lowest common clock. 48 MHz gives 10BASE-T 4.8, and 50 MHz gives USB FS 4.17, about 4 % off.
   - **Low speed is overstated** (`:350-351`: LS USB plus 10BASE-T "has the same issue"). Per the spec, the NRZ/NRZI receiver re-syncs on every transition (counter to P/2, `docs/isa.md:328-330`), and bit stuffing bounds the run between transitions.
     - At 50 MHz, LS is P = 33 against 33.33. That is −1.0 %, about 2.3 clocks of drift over 7 bits against a half-bit margin of about 16 clocks. 10BASE-T is exactly 5. So LS USB plus 10BASE-T at 50 MHz looks feasible on paper; this is my reading of the spec, not a test.
     - At 48 MHz, 10BASE-T is again 4.8, so the doc's point holds there.
   - **Effect on the recommendation:** small. The line unit is left out mainly on routing (grt3pkg's 518 overflows, though that run carries the whole ISA set). Correct the sentence: LS plus 10BASE-T is possible at 50 MHz on paper, and not at 48.

5. **Judging the recommendation.** These are my judgement points; the author picks.
   - **The third core's layout risk** is stated honestly: grt3 with 46 overflows, slightly above the 15–39 that detailed routing cleared at 6x4; pkg3 routed clean but untuned fails signoff (slow −1.571 ns, hold −0.318 ns, 22 slew); baselc8's +0.019 ns. Together with 3 h 06 min per pkg3 run, these support "high schedule risk".
   - **The plan does not show the cost of the fallback.**
     - The 2-core layout also misses 50 MHz at the slow corner (−0.643 ns), so it needs its own settings search and its own receipts.
     - Runner1 runs one harden at a time, so that search competes with the 3-core search for the same weeks 3–4.
     - The Dec 6 fallback then has to redo gate level, SDF, equivalence and precheck in week 5. That week is booked for the KV260.
     - Two ways out: budget the fallback search and receipts explicitly, or start the 2-core search first, since it is under a day, so the fallback is ready at the gate.
   - **The 48 MHz fallback clock is stated correctly.**
     - Base meets 48 MHz at every corner (my STA: slow worst flop +0.190 ns).
     - `flow/release_check.py --slow-mhz` takes 48, keeping typ and fast at 50 MHz. That fits "release_check.py at 48 MHz or better".
     - The 10BASE-T at 40 MHz figure, ±12 ns against 802.3's ±13.5, is `docs/guide.md:99` (a one-off model sweep), quoted correctly.
     - The doc says 48 MHz is "met at every corner (+0.19 ns)". The +0.19 ns is the slow corner; typ and fast have more margin.
   - **The FAQ points.**
     - The judging order (flexibility, then protocols, unique architecture, design and verification, write-up) matches the backlog record (AN).
     - Three points have no local copy and I did not check them: "more on-chip scores better" for USB and Ethernet, "at once or both roles", and "60 MHz is fine to request". The FAQ is an outside blog post, and I made no outside contact.

## Asked checks

- **Baseline harden rerun.** My run: `harden8x4.sh v069base` from my own runner1 clone at `65d0e8e`, under `~/protoemu-flow/verifier-069/`. It was the only harden running, and took 49 min.

  | | builder `base` | mine `v069base` |
  |---|---|---|
  | flow / signoff.py | 0 / PASSED | 0 / PASSED |
  | routing DRC / KLayout / LVS / antenna | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
  | stdcell utilization (area) | 0.340854 (378,074) | 0.340854 (378,074) |
  | GRT step: Metal3, total overflow | 45.67 % (142,683 / 312,432), 0 | 45.67 % (142,683 / 312,432), 0 |
  | setup WS typ / slow / fast | 0 / −0.6428 / 0 | 0 / −0.6428 / 0 |
  | slow violators, TNS | 90, −33.825 | 90, −33.825 |
  | hold WS typ / slow / fast | +0.1577 / +0.3615 / +0.0594 | +0.1577 / +0.3615 / +0.0594 |
  | slew / cap, every corner | 0 / 0 | 0 / 0 |
  | wire, hold buffers | 1,883,658, 517 | 1,883,658, 517 |
  | netlist sha256 | `982444c9e60151fc` | `982444c9e60151fc` |
  | GDS sha256 | `2d398ea6d90d1fa5` | `6632d9168b281f91` |

  - **The GDS.** The two files are the same size and record count. With the 202 BGNLIB/BGNSTR date records zeroed, both hash to `5cfd5256cb00634d`, so only the write dates differ. fo6, fo7 and ci7 show the same pattern: one netlist hash, different raw GDS hashes.
  - **`sta_period.sh` on my netlist.**
    - 20.833 ns: all corners met, slow worst flop +0.190 ns.
    - 16.667 ns: slow −3.976 ns, 509 endpoints, TNS −1130.417. Typ is met (worst flop +3.746 ns), and so is fast.
    - Both match the doc.
  - **fo6 reproduction.** On fo6 (read-only) at 20 ns, the script gives hold +0.191 / +0.362 / +0.095 ns and a slow worst flop of +0.010 ns, as claimed.
  - **fo6 figures.** fo6's Metal3 63.87 % (143,444 / 224,595) and 12 overflows at its GRT step, wire 1,788,162 and 233 hold buffers are from its logs and metrics.
- **The synthesis areas.**
  - I ran all of `run_all.sh` on runner1 with the same yosys (0.69+158, a1a0ad7d4). The output is identical to `syn/8x4-study/results.txt`. That covers `synth_chip.sh 2`, `3` and `4`, and every candidate row.
  - Every delta, per-core figure and budget percentage in the doc recomputes from it.
  - Flop counts (2 cores, against 1647):
    - TICK 1679 (+16 per core);
    - XCH 1775 (+64);
    - DRAM 2159 (+256);
    - CRC32 + CRCW 1713 (+33 per core against the base: CRC-32's 17 plus POLY_HI's 16);
    - TXCLR +1 per core.
  - Reading `pe_core_x.sv` and `pe_chip_x.sv`:
    - `V8_TICK` is a 16-bit counter of core ticks, readable as MOV source 15, which is free in v2.
    - `V8_XCH` is four shadow registers swapped by op 14 / sub 1.
    - `V8_DRAM` is `reg [15:0] dram [0:15]` with ld and st on op 14 / sub 2 and 3. Opcode 14 is reserved in `docs/isa.md:419`.
    - `V8_CRCW` gives a width of 1–32 (`{W32, W}`) and a 16-bit POLY_HI at core offset 0x11, behind its own clock gate.
    - These are what the doc describes. They are prototypes, unverified, and labelled as such.
- **`harden8x4.sh`.**
  - It edits only the run worktree: `info.yaml`'s tiles, `src/config.json`, and `src/project.v`'s defines (a no-op at 2 cores). The patched `gen_config.py` copy lives in a mktemp directory, and `tt` is copied in.
  - After my run, the run worktree showed only `M info.yaml`, `M src/config.json`, `exp.log` and `result.txt`. The source clone was clean, and no committed `flow/` file changed.
  - `--layout spread` with 2 banks gives exactly `corners-swap`'s macros: (4.32, 3.78) and (1302.72, 3.78), FS. It is also identical to the default submission config's MACROS. 3 banks give x = 653.28 for bank 2, and 4 banks sit 16.32 µm apart, as the doc says.
- **The doc against the record and the logs.**
  - **Builder runs (result.txt and logs):**
    - `baselc8`: −0.6235 ns, 80 endpoints, TNS −16.30, hold +0.148 / +0.343 / +0.047, PASSED, 45 min, netlist `73222029…`.
    - `grt3`: Metal3 65.38 %, 192,524 / 294,473, overflow 46 (40 on Metal3), placement 43.84 %, 344 extra iterations, 14 min.
    - `grt3pkg`: 73.26 %, 518 (489), 50.82 %, 736, 45 min.
    - `grt4f2`: DPL-0036 after "Inserted 6478 buffers", placement 52.51 %.
    - `grt4f2d58`: killed (rc 137) after 106 min.
    - `pkg3`, at the GRT step: 68.21 %, 200,868, 31 overflows (23 on Metal3).
    - `pkg3` final: stdcell 51.14 %; setup −1.5714 ns, 111 endpoints, TNS −60.0; hold −0.188 (3 endpoints) / −0.318 (6); slew 7 / 22 / 2, cap 1 / 1 / 1; netlist `d35b190c…`, GDS `65f302e3…`; 3 h 06 min. Its flow ran to the end and stopped on the deferred hold errors.
    - The runs went strictly one at a time.
  - **Critical path.** The builder's report shows `A_DOUT[3]` at 5.377 ns, about 58 cells, and `pin_out_cur[20]` of core 0, with arrival 21.858 against required 21.215.
  - **Late-clock default.** `--late-clock` defaults to 6 in `gen_config.py`, so baselc8 is a real change.
  - **physical.md.** t48m → t48r is −0.597 → −0.325, which is +0.27. The settings span is −1.627 to +0.243. physical.md also gives ±0.4 ns between placements, 15–39 overflows cleared at `GRT_ADJUSTMENT` 0.10, and 80–240 overflows at 0.15 stalling at 8 violations after six hours. It names 26 t48 runs, which fits "25–30".
  - **SRAM liberty (slow corner, `cell_rise`).** 256×32 is 5.13–5.44 ns, 512×32 is 6.38–6.69 ns, and 64×16 is 4.99–5.30 ns.
  - **SRAM LEF outlines.** 512×32 is 416.64 × 191.34 µm (79,720 µm²), 1024×32 is 140,183 µm², and 64×16 is 15,240 µm².
  - **Tiles.** 6x4 and 8x4 have the die areas given, and both have the same 43 pins on Metal4 at x 29.8–191.0 µm.
  - **Other record facts, confirmed:**
    - the 25 limitations;
    - the v3 bundle's −37 words, 241 → 213, 243/280 and 250;
    - its 39 tests and `eth10_rx_fcs`'s 27 words;
    - `usb_enum` 253 (a later version than v3's 241; not a contradiction), and `cec` 244;
    - CI's `lockstep_4core.xml`, which uses `PE_FIFO_LOG2=2` via `test/Makefile`;
    - `build.sh --cores 2 --fifo-log2 3`, `fpga_profile.py`'s 7 + 4 gated registers, ID 0x50450002, and the 6534 LUTs / 2545 FFs / 2 RAMB18;
    - `CLKOUT2_DIVIDE(CLK_DIV / 8)`, the line in `kv260_top.v`;
    - `eth10_tx.pasm`'s "the host computes the FCS";
    - the OCaml port's equivalence with `src/` (for the tapeout build).
  - The exceptions are findings 1–4.
- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits refs/remotes/public/main..HEAD`: 719 files, 7 messages, "TOTAL flagged: 0". The default store gives the same, and the 11 new files alone give 0.
  - My grep over the new files for host, tailnet, home path, email, CGNAT, SSID and password patterns found 0. Their only home references are the generic `~/ttpdk` and `$HOME/ttpdk` defaults.
  - All 7 commits are by the noreply identity and carry the trailer.
  - The branch base is `aa958a7` (public main plus 067/068). `merge-base --is-ancestor` on the old private tip exits 1.
- **ISA timing check:** not applicable. No `src/` change. The prototypes are not proposed RTL.

## What I ran

- **Checkout.** Detached at `65d0e8e` in my verifier checkout; I read the full diff (11 new files, +2639).
- **`remote_test.sh model` at `65d0e8e`.**
  - "rc=0"
  - "2916 passed, 29 skipped in 695.51s (0:11:35)"
  - SIGROK checks skipped: 28. A pass says nothing about them.
- **runner1** (under `~/protoemu-flow/verifier-069/`, one harden at a time):
  - the `v069base` harden;
  - `run_all.sh` (7 min 51 s, identical);
  - `sta_period.sh` at 20.833 and 16.667 ns on my run, and at 20 ns on fo6 (read-only);
  - liberty, LEF and DEF reads, and log reads of the builder's runs (read-only).
- **Locally:** the `spread` / `corners-swap` comparison (the script's own patch on a copy of `gen_config.py`, in scratch), greps of the record, and `record_check.py`.
- **A read-only helper** checked the record citations (post-freeze items, v3 study, CI, FPGA, multi-core). I re-checked each item it marked wrong before using it (finding 2).
- **Cleanup.** My runner1 clone, run worktree (2.4 GB) and the remote-test checkout `~/protoemu-flow/agent-runs/protoemu-verify-65d0e8e-1704879` have been removed. The builder's runs are untouched.

Summary: the baseline harden reproduces the builder's netlist exactly, synthesis reproduces byte for byte, and every routability and timing number matches its log. Fix the doc's misreported and misattributed figures (findings 1–3) and the low-speed USB sentence (finding 4). Then the study is accurate enough for the author's pick. Finding 5 is my input to that pick.
