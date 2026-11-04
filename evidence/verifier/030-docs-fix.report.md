VERDICT: FAIL

Commit 3410bc9 on base 2d05073 (an ancestor), 17 commits. Outside `docs/`, `README.md` and `evidence/` the commit changes only `fpga/kv260/README.md` (a 3-line link) and `test/model/test_hw_day.py` (the scan exemption). Nothing in `src/` or `flow/` changed. The author's statement, `docs/methodology.md` lines 1-36, is byte-identical to the base.

Most of it checks out:
- the FPGA coverage lists;
- the fault-fixes rows (`can_rx` has no RTL test);
- the supplementary SDF records, whose receipts are on runner1 with the stated hashes and 16/16 each;
- the speed figures;
- the 89 file:line references in the rewritten walkthrough;
- the nine author prompts, moved verbatim and unanswered;
- the Astra team facts;
- the 60 verifier files, byte-identical;
- the model suite.

It fails on three claims:
1. The methodology text, read as written, says what "Astra" meant in the author's 2026-10-06 sentence, which the records cannot say.
2. The USB logs are not "verbatim apart from the redactions": two more bytes changed.
3. Not every "report NNN" citation links its file.

Each fix is a sentence or a link.

| Claim | Result |
|---|---|
| 1. FPGA row and info.md: what ran on the KV260 and what did not; SWD model only | PASS |
| 2. Claims 2, 3, 4, 7, 13 | PASS |
| 3. Fault-fixes rows; `can_rx` model only | PASS: no RTL test loads `can_rx` |
| 4. Links, host-port speeds, PRISM credit | PASS, with notes 1 and 2 |
| 5. explain-wirelingo rewrite: references, nine prompts moved verbatim, none answered, no coaching voice | PASS |
| 6. methodology after line 37: Astra facts; no claim about what the author meant on 2026-10-06 | Facts PASS; **the second part FAILS** (finding 1) |
| 7. Evidence byte-identity, redactions, citations | verifier/ PASS, fabric-ping PASS, redactions complete. **USB logs not verbatim** (finding 2), **citations** (finding 3) |
| 8. Model suite 2349 passed, 28 skipped | PASS: `2349 passed, 28 skipped in 827.54s` |

## Blocking

1. **`docs/methodology.md:37-46` defines the author's "Astra" as the 2026-10-11 team.**
   - The text runs: "The author worked with Claude Opus 5.5 and Astra. In the project's records, Astra is `gpt-6-astra`, an OpenAI model run through the Codex CLI. From 2026-10-11 to 2026-10-14 it directed a team …". It then says the earlier reviewer "Codex" was "earlier Codex sessions, in the first week of October; the records kept here do not name the model those sessions used".
   - The author's sentence dates from 2026-10-06 (`git blame`: 6c46cd29, 2026-10-06, the same words as 277944a). That is five days before the Astra team was launched (`control/astra-director-launch.json`, `gpt-6-astra`, 2026-10-11 19:00).
   - Placed directly after that sentence, "In the project's records, Astra is `gpt-6-astra` … From 2026-10-11" reads as the meaning of the author's word. The records can't give it. The Astra the author meant on 10-06 may well be the first-week Codex sessions, whose model the same paragraph calls unrecorded.
   - The builder's own note to the author (`notes/author-questions.md`, last paragraph) says this is "one point only you can settle". The page doesn't say so.
   - **Fix:** one sentence after the author's, such as "That sentence was written on 2026-10-06, before the team below existed; the records do not say what it referred to then", or drop "Astra is".

   The facts themselves are right:
   - `assignments.json` lists five workers on `gpt-6.1-sol`: Euclid (Lean), Pulse (protocol firmware), Forge (flow tooling and release tests), Atlas (architecture, README and documentation) and Kepler (expansion study), plus Astra on `gpt-6-astra`.
   - `wind-down-20261014/canonical-commits.json` lists 17 commits, and all 17 are ancestors of `main` (I checked each).
   - Kepler's assignment is "read-only expansion analysis", so "experiments" (`:41`) is loose but not wrong.

2. **`evidence/kv260-usb-20261030/` is not "verbatim apart from the redactions below" (README `:18`).**
   - I extracted `~/wirelingo-usb-real-host-20261030.tar.gz` (sha256 `7d3a6160…01f2`, as the README says) into its own scratch directory and compared each file:
     - six are identical;
     - `ls/dmesg-final.txt` and `fs/dmesg.txt` each differ in four lines, not three.
   - The fourth line is `securefw securefw: securefw probed` (`:248` and `:249`). In the archive it ends in `\r\n`; in the commit, in `\n`.
   - `od -c` shows the byte, and the committed blobs have 0 carriage returns where the originals have 1 each. There is no `.gitattributes` and `core.autocrlf` is unset, so the copy step dropped it, not git.
   - Harmless in content, but these are presented as raw kernel logs with every change listed. **Fix:** restore the two bytes, or add the two lines to the redaction table.

3. **Not every "report NNN" citation links its file.** These are the unlinked ones:
   - `docs/verification-story.md:35`: "Passed on the third fix (report 009)". Its link is only at `:105`.
   - `docs/verification-story.md:105`: "the fixes for reports 000, 001 and 004".
   - `evidence/fabric-ping-20261103/README.md:36`: "(report 029, finding 2)". The same README links 029 at `:4`.
   - `docs/rules-check.md:145`: "Verifier report 009 is `VERDICT: PASS`", and `:154`: "Reports 000-023". That is a dated audit, but these are the very rows whose fix the item claims.

   All 75 links that do point into `evidence/verifier/` resolve, and so does every relative link in the changed Markdown files. The anchors added in this range resolve too.

## Checked

**Claim 1.** I listed every `firmware/*.pasm` and looked each up in `fpga/kv260/README.md`.
- Every program the README row says ran there does appear in a result there. That includes the post-freeze ones: DMX512 `:335`, DShot `:337`, I2S `:336`, Wiegand and LIN `:320-325`.
- None of the "not run there" list has a KV260 result:
  - SWD is "Not run (parked)" (`:921`);
  - `usb_ls_rx` + `usb_ls_tx` and the USB-UART pairs are "Not run" (`:423`, `:843`, `:930`);
  - `eth10_reply`, `pps_capture`, the MDIO, QSFP and PMBus bench, `rmii_*`, `hd44780_4bit` and `ata_pio` do not appear at all.
- `uart_rx_batch4`, `uart_rx_overlap4` and `uart_rx_envelope3` are not run (`:921`). They are the UART half of the USB-UART pairs, which the row lists.
- info.md drops SWD from the "verified" list and adds "exists on the model only, and is parked". The model is all SWD ever had.
- "CAN, JTAG, 1-Wire, PS/2, WS2812 and the I2C sniffer are checked on the cycle model": no cocotb file under `test/` loads any of them. The only `can_rx` references outside `test/model/` are mutants that run model tests.
- Nothing true was weakened. Each changed row keeps its KV260 and gate-level history, now said to be with the older firmware.

**Claim 2.**
- **Report 009:** `VERDICT: PASS` for `0bdcbe5`; "delay N = 1, 3, 100 fail"; "the `eth10_match` fixes from 000, 001 and 004 are complete".
- **Post-freeze SDF:** `release-evidence.md:232-237` gives receipts `pfsdf-61af9b2/test/sim_build/gl_sdf.I0i5IJ` and `gl_sdf.QWI1Yc`.
  - On runner1 (read only), their `receipt.json` sha256 are `2d16097a…90b1` and `fdae9400…3f2a`, as written.
  - Their `results.xml` hold 16 test cases with 0 failures at slow and at fast.
  - The wording is the same in README (two places), post-freeze-protocols.md and verification-story.md: supplementary, single-case, outside the release gate, no verifier report.
- **Claim 4:** methodology Limits now matches `equivalence.md:9-13`.
- **Claim 7:** `usb_stress.py` sends `GET_DESCRIPTOR` setups. `fpga/kv260/README.md:1286` and `:1334` count 10,000 and 56,000 "Live `GET_DESCRIPTOR`" reads, and 10,000 + 56,000 = 66,000.
- **Claim 13:** claims-audit is marked historical, and recommendation F is marked done, with the receipts above.

**Claim 3.**
- `test/test.py:276` `test_uart_rx_glitch` and `:613` `test_i2c_master_bus_fault` exist and exercise the mid-bit check and the SDA read-back.
- `usb_enum` is loaded by the RTL autonomous tests.
- `can_rx` has no RTL test, so "model only" is right.

**Claim 4.**
- **The README bullets:**
  - fault-testing.md: six programs in the sweep table (`can_rx`, `eth10_match`, `eth10_rx`, `i2c_master`, `uart_rx`, `usb_enum`), six weaknesses, four fixed;
  - blind-test.md: HD44780 in 99 words; ATA PIO doesn't fit as specified, but an 8-bit subset does.
- **Speeds:**
  - 199.9, 193.6 and 183.6 Mbit/s are in `fpga/kv260/README.md:198-199` and `:999`, the last on the 45.93 MHz alternate MMCM;
  - `quad_spi.py:85-86` refuses SCK above half the project clock, and 24 MHz × 4 = 96 Mbit/s;
  - info.md labels the MicroPython figure an estimate.
- **PRISM:** `src/pdn_cfg.tcl`, `flow/odb_stripes.py` and `flow/odb_sram_diodes.py` say "Copied from kdp1965/ihp-um-janestreet-prism" in their first line. `librelane_plugin_protoemu.py:4` says "Adapted from". No other tracked source file mentions PRISM.

**Claim 5.**
- The old page (`2d05073`) has 9 `AUTHOR TO ANSWER`; the new page has 0.
- Each of the 13 quoted blocks in `notes/author-questions.md` occurs verbatim in the old page (checked by exact substring).
- None of the eight questions is answered on the new page:
  - "Known limits" says "None of them is ranked here";
  - "Who decided what?" quotes only what `methodology.md:150` and its tables record;
  - there is no cost or regret text, and no "why a network match".
- No coaching voice: the only "you" is "a trace you can rerun" (`:90`), and there is no "memorise", "out loud" or "be able to".
- All 89 `path:line` references resolve inside their files, and I read each target line. Each is the thing cited (for example `verification.md:388` "34 of 34 mutants killed", `physical.md:372` "99.8 %, 24.1K").
- The claims-audit counts (5, 6, 21, 12, 2) match its table.

**Claim 7.**
- All 60 inbox files 000-029 `cmp` equal to `evidence/verifier/`, and there is no extra file besides the README.
- Its table's verdicts (15 PASS, 15 FAIL) and commits match each file.
- The four `evidence/fabric-ping-20261103/` data files `cmp` equal to `evidence/029/`.
- The redaction search over all of `evidence/` found:
  - no `*.ts.net`, 100.64/10, `192.168.` or provider name, and nothing key-like;
  - for `@`, only systemd unit names (`modprobe@loop.service`);
  - no Wi-Fi or SSID string;
  - no MAC: the eth0 MAC `[MAC redacted]` occurs nowhere in the tree;
  - none of the user's email address.
- The two kernel-banner emails and the MAC are replaced where the README says.

**The test change.**
- Only one non-README file under `evidence/` matches the chained-load pattern: `verifier/022-guard-fix.report.md:40`, which quotes the docs it found.
- In a throwaway copy, one appended `unloadapp; … loadapp` line fails `test_nothing_tells_anyone_to_chain_the_load` in each of two places:
  - in `docs/guide.md`: `1 failed`;
  - in `evidence/kv260-usb-20261030/README.md`: `1 failed`.
- The stock copy passes.

## Notes

1. `README.md:99` "the demo board's MicroPython driver is far slower" is an unmeasured estimate without the label. The label is in `info.md:81`, which it links. Add "(an estimate)" there too.
2. "as each file's header says" (README PRISM bullet): `flow/gen_config.py`'s header doesn't mention PRISM. The credit is in the config text it writes (`:153-154`, and so `src/config.json:13`) and in a `--every-other` help string (`:88`).
3. `evidence/fabric-ping-20261103/README.md:25-26` says the reply "is read off TD inside the FPGA by a sampler on core 1".
   - The code puts `usb_sampler` on `ethhost.RX_CORE` = core 0 (`fabric_eth.py:289` at da82a51; `ethhost.py:36`). Core 1 sends.
   - Item 029 said "usb_sampler on core 1" too. My 029 report did not catch it.
4. `docs/rules-check.md:159`'s "the strongest results" (report 026, note 1) is unchanged. It is still only a superlative about our own results.

## What I ran

- `remote_test.sh model` at 3410bc9: `model rc=0`, `2349 passed, 28 skipped in 827.54s (0:13:47)`, "SIGROK CHECKS SKIPPED: 28 tests" (checkout `agent-runs/protoemu-verify-3410bc9-811677`).
- In `~/verifier-030/`, a `git archive` copy: `test_nothing_tells_anyone_to_chain_the_load`, stock (`1 passed`) and with each of the two planted lines (`1 failed` each).
- **On runner1, read only:** `sha256sum` and a test-case count of the two `pfsdf-61af9b2` receipts.
- **Locally, read only:**
  - `cmp` of `evidence/verifier/` against the inbox, and of `evidence/fabric-ping-20261103/` against `evidence/029/`;
  - the USB tarball extracted into its own scratch folder, with `cmp`, `diff` and `od -c`;
  - the redaction greps;
  - link and anchor checks;
  - the 89-reference resolution;
  - the verbatim check of the moved prompts;
  - the team records in `~/wirelingo-team-20261011/control/`;
  - the 17 commits checked against `main`.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
