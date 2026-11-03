VERDICT: FAIL

Commit 7e19431, base 6d5bdd5 (`improve-2026-11` is now f3a9db2, which changes only `test/model/test_hw_day.py`, so every line cited still holds). The document is substantially right, and most of it checks out against its sources:
- the competition text and the Advent of FPGA rules;
- all 16 claims about our docs;
- the repo state;
- the rival facts I checked, and the PRISM credits.

It fails on claim 5: the doc makes an unqualified "only" claim, which RULES.md forbids. Claim 1's "verbatim" also fails in a few small places. It also misses that GitHub Actions is **disabled** on our repository, which changes ranked fix 7.

| Claim | Result |
|---|---|
| 1. Quotes verbatim and correctly attributed; no rubric, FAQ, eligibility, AI rule or form | Mostly. Every Jane Street sentence quoted is on the page. Four small misquotes or misattributions (finding 2) |
| 2. TT FAQ on a failing GDS action; private runners 2 CPU / 8 GB; zero Actions runs | PASS. The runner figure is GitHub's, not TT's, as the doc says. Actions disabled is missed (finding 1) |
| 3. The 16 doc claims | PASS, all 16, at the cited lines |
| 4. Rival table | PASS on what I checked. Two framing notes (note 4) |
| 5. RULES.md | **FAIL** (finding 3). Otherwise clean: only `docs/rules-check.md` changed, the author's statement is untouched, no contact |
| 6. Missed by the reviewer | Finding 1, note 5 |

## Findings

1. **Missed, and it changes fix 7: Actions is disabled.** `gh api repos/josusanmartin/wirelingo/actions/permissions` gives `{"enabled":false}`.
   - That is why `actions/runs` has `total_count` 0. The four workflows are present, and `gds.yaml` uses `tt-gds-action@ihp-cmos5l` with `pdk: ihp-sg13cmos5l`.
   - So "make the repository public and let the GitHub Actions run" does nothing by itself. Actions must be turned on too, in the author's settings.
   - "Zero runs" says nothing about whether the action would pass, which the doc's §2 risk cell half-implies.

   Facts confirmed:
   - `"private": true`;
   - pushed 2026-10-30T20:17:22Z;
   - the action's default LibreLane is `3.1.0.dev3`;
   - `install_sg13cmos5l.sh` pins IHP-Open-PDK `2bbec755…`, which is `runner1:~/ttpdk`'s HEAD;
   - the default `tools-ref` is `ihp-sg13cmos5l`, whose head is `d66cf179`.

2. Quote and attribution slips (claim 1 says verbatim). I checked each quote against the fetched page text:
   - `rules-check.md:16`: `"submit your design"`. The page has "Submit your design by January 18th, 2027." It is a fragment, recased.
   - `:70-73` (TT FAQ):
     - The required fields are a bullet list on the page ("we require the following fields to be filled: author title description how_it_works how_to_test language") about the Documentation action. The comma-and "quote" is the doc's own.
     - "At least 50MHz" sits under "Chip Specs for TT04 to TT10", the SKY130 shuttles.
     - The clock page's "maximum input frequency of 66 MHz" is the `sky130_ef_io_gpiov2_pad` rating.
     - So the §2 row "TT clock (≥ 50 MHz, input ≤ 66 MHz) | met" applies SKY130 figures to an IHP chip. The doc does say "nothing IHP-specific" for the GPIO page, and should say the same here.
   - `:63`: the template's last commit (2026-10-19) is `231f26a5` "fix(test): add sg13cmos5l UDP models to gate-level simulation". "chore: switch from ihp-sg13g2 to ihp-sg13cmos5l" is the one before it, the same day.
   - `:123`: explain-wirelingo is quoted as "so you can stand in front of an engineer". The text (`explain-wirelingo.md:4`) is "The goal is that you can stand in front of an engineer".
   - `:109`: 'isa.md and board-transport.md say "unmeasured"'. They say "Board throughput and pad timing require separate measurements" (`isa.md:635-636`) and "Still to measure on a board" (`board-transport.md:75`). The meaning is right; the quote is not theirs.

3. **RULES.md: "only".** `rules-check.md:205`, on Escapement: "The only other public 6x4 entry with our macro". RULES.md: "Never claim first, only, fastest or 'nobody else'."
   - It is also unproved by the doc's own account: "more than 150 candidates", and only the ones with real content were read.
   - openPIO's "4 SRAMs" and TejasDasa's "CFGMEM macros" are not named. I found no `256x32` file in either tree, but that doesn't settle it.
   - Write "the other 6x4 entry in this table with the same macro".
   - "Strongest on proved timing" in the same cell is a superlative the table can't support either.

## Notes

4. Rival framing:
   - **MarcosAsh.** The comparison says ours "depends on host software" and theirs "can be downloaded".
     - True as far as it goes: the release `evidence-2026-10-15` has `board-evidence-2026-10-15-keyboard.tar.gz`.
     - In it, `keyboard.kernel` shows `new low-speed USB device number 70 using xhci_hcd` and `input: HID 1209:0001 Keyboard` on a separate PC, and `pico_b.log` shows it typing "hello jane street".
     - But their run is also driven by a Pico as the chip's host: `run.txt` copies `usb_device_firmware.py` and `demo_usb.py` onto a Pico. Say so, or drop the contrast.
   - **mrnathanhumphrey-droid.** What is committed is decoded records written from a DSLogic capture (`fpga/witness/i2c_fram_hw_witness.json`, `onewire_hw_witness.json`: "a RECORDED BENCH WITNESS … not a hardware run in CI"), not raw captures. Its README says the same, and that the UART run is not a witness. "Captures committed" overstates it slightly.

5. Missed: `docs/info.md:46-48` says "Most of it (not SWD or the two-core USB device) has also run on an FPGA build". That is the same overclaim as README:31, item 1, and the datasheet is where a judge reads it. `pps_capture`, `mdio_*`, `eth10_tx_cs`, `rmii_*`, the bench firmware and the blind-test firmware appear nowhere in `fpga/kv260/README.md` (my grep: 0 hits each).

6. Two framing points from the post the doc leaves out:
   - "we'll pay to fabricate our favorite designs!" sits beside "most novel designs". Selection is the organisers' taste, not only novelty.
   - "Tiny Tapeout has examples of SRAM running on this process node you can reference" bears on §2's SRAM risk. It links the ttihp0p2 1024x8 test, the same macro TT's memory page names, so the doc's conclusion stands.

## What held

- **Claim 1:**
  - Every quoted Jane Street sentence is on the page (fetched 2026-11-03), and so are the authors, Benjamin Devlin and Anish Singhani, and the date, Sep 10, 2026.
  - The page has no rubric, FAQ, eligibility clause or AI-use rule, and no form ("We'll add a final submission form to this page closer to the deadline!").
  - The Advent of FPGA rules on `blog.janestreet.com/advent-of-fpga-challenge-2025/` have "Original work only : no duplicates or obviously AI-generated submissions. You should be able to explain your design."
  - TT's memory page has the 1024x8 and "not trivial" sentences; its table lists 256x32 with "3x4" minimum and does not call it tested.
- **Claim 2:**
  - TT FAQ: "A project can't be submitted to a shuttle if its GDS action is failing".
  - GitHub's runner page, private repositories: Linux x64 is 2 CPU, 8 GB.
- **Claim 3**, all 16 at the cited lines:
  - **1:** `fpga/kv260/README.md` has 0 mentions of the listed firmware, and says the USB-UART pairs were "Not run".
  - **2:** report 009 is `VERDICT: PASS` for `0bdcbe5`.
  - **3:** README:94 says typ/slow/fast; README:286 says typ only; verification-story says no report. The receipts are at `release-evidence.md:229-233`.
  - **4:** methodology.md:144 is under "## Limits", outside "In the author's words" (lines 3-34), so the fix doesn't touch the author's statement; equivalence.md proves fo6's netlist (`a94a948b…`, 7429 points).
  - **5:** 355 of 433 commits carry the Claude trailer.
  - **6:** info.md:60-61 is unqualified. The default MicroPython driver bit-bangs (`protoemu.py:4`); fix 3 should also mention the optional `quad_spi.py` PIO/DMA backend that the driver points to.
  - **7:** 10,000 and 56,000 `GET_DESCRIPTOR` reads.
  - **8:** no kernel log or `usb_stress` output is tracked.
  - **9:** README links neither fault-testing.md nor blind-test.md.
  - **10:** SWD is in the "verified in simulation" list and is model only.
  - **11:** "not published with this repository".
  - **12:** `git diff --shortstat 7685aba 6d5bdd5 -- firmware/` is `11 files changed, 1118 insertions(+)`, all new files. The 2026-11-02 SDF receipts are for `test_post_freeze`, not these.
  - **13:** claims-audit:151 against `release-evidence.md:216`.
  - **14:** the jargon is present in all three docs.
  - **15:** methodology:37 "Astra".
  - **16:** 43 docs and 15,275 lines before this one; guide.md is at README:139.
- **§2 figures:**
  - six `substituting_steps` in `src/config.json`;
  - fo6's 4-CPU reproductions took 1 h 46 min (ci7) and 1 h 57 min (ci6);
  - fast hold +0.095 ns;
  - 47 %/53 % (t48ba's; fo6 is t48ba plus one buffer);
  - `PE_FLOP_IMEM` exists for simulation only.
- **Claim 4:**
  - The last-commit dates and counts of all 17 rivals match the GitHub API, apart from Loom: now 143, after commits today.
  - 8x4: `info.yaml` says `tiles: "8x4"` on Loom (moved on 2026-10-06, `gds` green on main that day), PRISM and JLNuijens.
    - PRISM's `gds.yaml` uses `custom_gds` with `tools-repo: kdp1965/tt-support-tools`, `tools-ref: cmos-8x4`: the "(custom)" is right.
    - `d66cf179` is "feat(ihp): 8x4 tile size for ihp-sg13cmos5l", 2026-09-21, and is the branch head.
    - The SRAM example (created 2026-11-02) lists 8x4; the Verilog template's comment does not, so "newest CMOS5L template" in §2 must mean the SRAM example. Say so.
  - Quotes found in their sources:
    - Escapement "built and timed but not yet run on a board", and 33 MHz at 30 ns;
    - PEMU, both quotes;
    - Loom "Nothing has run on hardware" and the 2026-09-28 organiser answer (`docs/DECISIONS.md:241`);
    - PRISM "the size allowed by the Jane Street competition";
    - fipe;
    - weft, both;
    - ProEm;
    - Kanishk234's two lines (the second with the source's own missing spaces).
  - PRISM reuse: our tree has the same blobs as PRISM only for TT-template files. `flow/odb_stripes.py`, `flow/odb_sram_diodes.py` and `src/pdn_cfg.tcl` equal PRISM's apart from the one added credit line. `librelane_plugin_protoemu.py:4` says adapted, and `gen_config.py` and `config.json` name it. README has no credit, as the doc says.

## Ranked fixes

| Fix | Judgement |
|---|---|
| 1 Docs wording | **Agree** |
| 2 Link the weaknesses | **Agree**. After 023 merges, the README rows must also say the fixes are model and RTL only (report 023 finding 1) |
| 3 Host-port speeds | **Agree**; also mention `quad_spi.py` |
| 4 Credit PRISM | **Agree**; the file headers already do it |
| 5 Judge's page | **Agree**, the author's call |
| 6 explain-wirelingo | **Agree**, the author's call |
| 7 Public + Actions | **Agree it is the most valuable, but incomplete**: Actions must be enabled as well (finding 1). On a public repo the runner is 4 CPU / 16 GB, which the 1 h 46-57 min reproductions used |
| 8 Rerun the release gate | **Agree** |
| 9 Publish reports and logs | **Agree**, the author's call |
| 10 Third-party far ends | **Agree**, but nothing on the board until the author lifts the Kria ban |
| 11 8x4 contingency | **Agree** |

None is wrong. Fix 1's "minutes each" holds.

## What I ran

All read-only:
- `curl` and WebFetch of the Jane Street post, the Advent of FPGA post, TT's FAQ and clock, GPIO and memory pages, the ttihp0p2 SRAM test page, and GitHub's runner reference. Each quote was checked by exact string match after tag stripping.
- `gh api` for our repository's visibility, runs and Actions permissions; for TT's template, tools, action, SRAM example and staging repos; and for each rival's metadata, last commit, `info.yaml`, workflow runs and the files quoted.
- MarcosAsh's 1.2 MB evidence tarball, extracted in scratch, read and deleted.
- `runner1:~/ttpdk`'s git HEAD.

Nothing was run on the Kria, nothing was pushed, and no one was contacted.
