VERDICT: PASS

Commit f3829df, parent 7e19431. Only `docs/rules-check.md` changed (`git diff --name-only`). Nothing under `src/`, `flow/` or `docs/methodology.md` changed. Every finding in report 024 is fixed, and every new or changed sentence I checked against its source holds. Three minor notes follow.

| Item | Result |
|---|---|
| 1. Actions disabled | PASS |
| 2. Quotes | PASS |
| 3. RULES.md | PASS for RULES.md. The item's "no superlative anywhere" is slightly overstated (note 1) |
| 4. Notes 4-6 and the ranked-fix judgements | PASS; note 2 on the MarcosAsh bullet |
| 5. Nothing new is wrong | PASS, apart from notes 2 and 3 |

## Checked

**1. Actions.**
- `gh api repos/josusanmartin/wirelingo/actions/permissions` still gives `enabled` = `false`.
- The GDS row (`:112`) cites it, and says zero runs settles nothing either way.
- The new claim that local runs "call the same `tt_tool.py --harden`" holds: `flow/harden.sh:60` runs `python ./tt/tt_tool.py --harden --ihp`.
- Fix 7 (`:188-197`) now says both publishing and enabling Actions are needed, and that this is the author's job in the repository settings.
- The runner figures are right: 4 CPUs / 16 GB for a public repo, 2 / 8 for a private one.
- The local runs do match that budget: `physical.md:494` (ci7: 4 CPUs, 16 GB, 1 h 46 min) and `:505` (ci6: 4 CPUs, 16 GB, 1 h 57 min).
- "The only fix that turns…" is gone.

**2. Quotes.** I fetched the post again today and matched each quote against its text with the tags stripped:
- "Submit your design by January 18th, 2027." is on the page (`by <strong>January 18th, 2027</strong>.`).
- "This time, you're designing the chip, and we'll pay to fabricate our favorite designs!" is on the page.
- "For instruction memory, SRAM can be more area-efficient than flip-flops. Tiny Tapeout has examples of SRAM running on this process node you can reference." is on the page.
- The post's SRAM link is `tinytapeout.com/chips/ttihp0p2/tt_um_urish_sram_test`, whose page is titled "SRAM (1024x8) test". So "the 1024x8 macro, not the 256x32 we use" is right.
- The FAQ field list is now marked as paraphrase.
- "At least 50MHz" is labelled as being from TT04-TT10 (the SKY130 shuttles), and 66 MHz as the `sky130_ef_io_gpiov2_pad` rating.
- The TT clock row says "met against SKY130 figures only".
- "TT publishes nothing for the IHP pads or clock input" holds. I checked the FAQ and all nine `/specs/` subpages, and the only IHP text is in `analog` (template links) and `memory` (SRAM macros).
- Our own docs:
  - `isa.md:635-636` "Board throughput and pad timing require separate measurements" is verbatim.
  - `board-transport.md:75` "Still to measure on a board" is verbatim.
  - `explain-wirelingo.md:4-5` "The goal is that you can stand in front of an engineer" is verbatim.
- The template's last commit: `gh api` gives `231f26a5` "fix(test): add sg13cmos5l UDP models to gate-level simulation", committed 2026-10-19T10:52:36Z.
  - Before it is `9bee90bb` "chore: switch from ihp-sg13g2 to ihp-sg13cmos5l", with the same commit time. Its author date is 2026-06-24, because it was rebased that day. So "the same day" is right by commit date.
- The doc uses straight apostrophes and the page uses curly ones. That is typography only.

**3. RULES.md.**
- Every 024 hit is gone: "The only other public 6x4 entry", "Strongest on proved timing", "nobody has silicon", "the only fix", "larger in count".
- I grepped the whole file for first, only, fastest, nobody, no one, sole, unique, best, strongest, largest, larger, most, better and leading. What remains:
  - wording inside quotations ("Original work only", "unique functionality", "most novel");
  - "only" limiting our own evidence (`:112`, `:118`, `:127`, `:129`, `:153`, `:174`);
  - "first" meaning the order of reading (`:144` "first screen", `:160` "where a judge reads it first", `:165` the "(cheapest high-value first)" heading, `:182` "to look at first");
  - the meta line at `:162`;
  - the quantifiers at `:217` and `:261`;
  - the superlative in note 1.
- None of these claims we are first, only or fastest.

**4. Notes 4-6 and the fix judgements.**
- **MarcosAsh Pico host.** I downloaded and extracted their evidence tarball again in scratch, then deleted it.
  - `run.txt:19` copies `usb_device_firmware.py` and `demo_usb.py` to a Pico with `mpremote`.
  - `run.txt:35` then runs `demo_usb.py` there.
  - So the chip's host is a Pico running MicroPython, as the doc says.
- **The rival's decoded DSLogic records.**
  - Their `model/gate_i2c_hw_witness.py` docstring reads "A RECORDED BENCH WITNESS, RE-CHECKED IN CI … It is NOT a hardware run in CI". It says the CSV captures stay out of git.
  - `ci/shards.txt:69-70` lists both witness gates. The latest `dev` test run is `success` (2026-10-26).
  - So "records decoded from logic-analyser captures committed and re-checked in CI" is right.
- **Claim 17 (`:160`).** It matches `info.md:46-48` verbatim. Fix 1 now includes item 17, and the FPGA row cites it.
- **The post's lines.** "favorite designs" is in the selection bullet (`:24-28`). The SRAM pointer is in Fit and in the SRAM row (`:120`).
- **Fix 2** (`:172-174`) now says the fault fixes are verified on the model and in RTL simulation only, which agrees with report 023.
- **Fix 3** (`:175-179`) now mentions `quad_spi.py`. The driver does point to it (`tools/micropython/protoemu.py:5`), and `board-transport.md:75` supports "unmeasured on a board".
- **The 8x4 row** (`:113`) now names TT's tools at `d66cf179` and the SRAM example's `info.yaml`, and says the Verilog template's comment doesn't list 8x4. That agrees with what I found for 024.

## Notes

1. **Item claim 3 is a little too broad.** `rules-check.md:159` still says "A judge with an hour will not find the strongest results".
   - It is unchanged since 7e19431.
   - It ranks our own results against each other, not against a rival, so it does not break RULES.md.
   - `:261`'s "almost every serious rival" is a hedged count, not a superlative.
   - "No superlative left anywhere" is therefore not literally true, but no forbidden claim remains.

2. **The MarcosAsh bullet (`:248-254`) dropped a true difference.** "The difference is that our evidence is a transcript in a doc" implies it is the only one.
   - The old text also said "the host is the same board as the FPGA", and that still holds.
   - Theirs: `journalctl -k` (`run.txt:38`) is on the PC that drives the Picos, a separate machine from the Icepi Zero.
   - Ours: the USB host is the Kria's own Linux xHCI, on the board that holds the FPGA. The Wirelingo table row still says so.
   - Write "a difference", or keep both.
   - A smaller point: the row's citation `pico_b.log` belongs to the second Pico, which listens on UART (`run.txt:33`). The host is the Pico that runs `demo_usb.py` (`run.txt:35`).

3. "Ours the Kria's CPU over the SPI port" (`:252`) is right in substance.
   - Linux drives the host port through AXI GPIO, or through the fabric SPI master `pe_spim`, which the USB runs used (`fpga/kv260/README.md:16-17`, `:193`).
   - The "SPI port" is reached through fabric logic, not a CPU SPI peripheral. That is fine for this table.

## What I ran

All read-only. Nothing touched the Kria, SWD or runner1, nothing was pushed, and no one was contacted.
- `git diff 7e19431 f3829df` (one file, 55+/36−) and `git diff --name-only`.
- A grep of `docs/rules-check.md` for the RULES words, and the same grep on the 7e19431 version to list what was removed.
- `curl` of the competition post, `tt_um_urish_sram_test`, the FAQ and every `/specs/` subpage. Each quote was checked by exact substring match after tag stripping.
- `gh api`:
  - our repo's `actions/permissions`;
  - the CMOS5L template's last 3 commits on `cmos5l`;
  - the mrnathanhumphrey-droid repo's workflows, `ci/shards.txt`, `model/gate_i2c_hw_witness.py`, a code search for "witness", and its last runs on `dev`.
- `gh release download evidence-2026-10-15 -R MarcosAsh/protocol-emulator`, then read `run.txt`, `pico_b.log` and `ledger.txt`, then deleted it.
- Local reads of `flow/harden.sh`, `physical.md:494-505`, `isa.md:635-636`, `board-transport.md:75`, `explain-wirelingo.md:4-5`, `info.md:46-48`, `tools/micropython/protoemu.py:5` and `fpga/kv260/README.md`.
- Test suite: not run. The commit changes one Markdown file. I found no test that reads `rules-check.md`; the tests that read docs read others, such as `hardware-day.md` and `fault-testing.md`. So no test result can change.
