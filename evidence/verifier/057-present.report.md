VERDICT: FAIL

Item 057 `present`: commits 29d2799, c8a9fbc, 05db761 and 18a5c09 on e449e40, checked out detached at 18a5c09. I read the diff against e449e40 and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

Two findings block it. Both are in the headline text a judge reads first, and both are quick to fix. Everything else checks: the LINECFG decodes, the cited lines, the counts, the clock figures, status.md as a verbatim move, the link checker and the privacy of the added text.

## Findings

1. **The protocol table marks SPI "R yes" for firmware that has no RTL test** (`README.md:92`).
   - The row is "SPI master: modes 0-3, packed, 4 clocks per SCK" and lists `spi_master`, `spi_master_modes`, `spi_master_packed` and `spi_master_fast`.
   - Its R cell is a bare `yes`. Elsewhere the table names firmware when only some of a row reached a level: R "master, target" for I2C, G "`spi_master`, `spi_master_fast`" in this same row. So a bare `yes` reads as all four.
   - `spi_master_modes`, the only firmware with modes 1-3, has no RTL simulation test. `grep -rln spi_master_modes tools test fpga` finds only model tests and the KV260 host script: `test/model/test_spi_modes.py`, `test_spi_modes_timing.py`, `test_micropython_spi_uart.py` and `test_export_program.py`. Nothing under `test/*.py` is cocotb.
   - `spi_master` is mode 0 only (`README.md:240`).
   - `spi_master_modes` is also missing from the full firmware table (`README.md:240-241` has `spi_master` and `spi_master_fast` only), so the reader can't find its evidence anywhere in the README.
   - Claim 6 says every mark has evidence behind it. This one doesn't.
   - Fix: R becomes "`spi_master`, `spi_master_packed`, `spi_master_fast`" (modes 1-3: model and FPGA), and `spi_master_modes` gets a row in the full table.

2. **The new headline line-unit wording contradicts the repo's own explainer, which this branch edited.**
   - The new text:
     - `README.md:43`: "Everything protocol-specific is software, loaded after the chip is made."
     - `docs/summary.md:25-26`: "Nothing protocol-specific is fixed in the silicon".
     - `README.md:49-50` and `summary.md:30-31`: the line unit "has no protocol built in".
     - `docs/line-unit-settings.md:6`: "Nothing in it is specific to one protocol."
   - `docs/explain-wirelingo.md:582-585`, which the README links for readers new to hardware, says: "So the line unit is protocol-specific hardware, and 'protocols are firmware' holds for the cores, not for it." The branch changed two other places in that file (`:499`, `:589-591`) and left this paragraph.
   - `docs/rules-check.md:122` also says "The line unit is protocol-specific hardware."
   - A judge who reads the README top and then the explainer meets opposite statements on the very point the item puts first.
   - On the substance, the README's register-write description is accurate (claim 2, below). "Everything protocol-specific is software" goes further than that. The line unit's codings are fixed logic: NRZI, ones-stuffing, Manchester phase tracking, and the SE0 and TP_IDL end patterns (`isa.md:271`, END 1 = "SE0 then IDLE"). Software picks among them; it can't add one.
   - Suggested wording: "No protocol is fixed in the silicon. The cores run firmware. The line unit offers a fixed menu of line codings, stuffing rules and end patterns (built with USB and 10BASE-T in mind; CAN-style stuffing also fits), and the host picks from it with three register writes at load."
   - Then reconcile `explain-wirelingo.md:582-585` and `rules-check.md:122` with whichever wording the author keeps.

### Notes (not blocking)

3. **The 10BASE-T half of "a core alone is too slow" has no budget or test behind it** (`README.md:54-56`, `summary.md:33-35`, `line-unit-settings.md:14-21`).
   - The USB half is backed: `usb_ls_rx`'s loop is 13 instructions per bit on its one-path and 14 on its zero-path. I counted them from the tick comments (`firmware/usb_ls_rx.pasm:78-130`) against isa.md §4.1's one instruction per tick, which is ≥ 13 against a 4-clock bit.
   - For 10BASE-T the page gives only the unit's mechanism (both-edge sampling, phase tracking), not a firmware budget or a failed firmware receiver.
   - Either cite one, or say what the unit does that a core can't ("samples at both clock edges and tracks the phase; a core sees one sample per clock").

4. **"The demos run at 48 MHz, with margin"** (`README.md:152-156`, `summary.md:96-100`, `info.md:18-20`).
   - fo6 has no STA at 20.833 ns in `physical.md`. The 0.83 ns is arithmetic (20.833 − 20).
   - It does hold empirically. In all 21 STA rows of `physical.md:233-253`, the 48 MHz slack is the 50 MHz slack plus exactly 0.833 ns. t48ag, for example, shows +0.054 / +0.887.
   - The README should say that the margin is derived, not measured on fo6.
   - Also, "10BASE-T is tested at 40 and 50 MHz" leaves out that the 802.3 ±13.5 ns jitter result needs 50 MHz, where the slack is +0.010 ns. At the demo board's 40 MHz (`info.md:19`, `:129`) it tolerates ±12 ns, from a one-off model sweep (`guide.md:99`).
   - `summary.md`'s results row correctly says "at 50 MHz". The physical-status lines should say it too.

5. **The table under-claims two gate-level results.**
   - `release-evidence.md:240-245` records supplementary functional gate-level runs on fo6 at validation 61af9b2: `test_usb_uart_rtl` 3/3 and `test_spi_packed_host` 1/1.
   - The README's G column is blank for "UART to a PC over USB" and omits `spi_master_packed`.
   - This follows the stated rule (the 25 main-suite tests plus the post-freeze and network-stack runs only), but a reader takes a blank as "not at gate level". Add them with the same "supplementary" wording, or say in the legend that host-workflow modules are left out.

6. **`docs/eth10-network.md:37` still says "not yet on the board".**
   - Claim 12's two edits are correct: `:7-11`, and the parenthesis at `:287-288`. Report 037 is `VERDICT: PASS`, and `evidence/fabric-ping-20261103/README.md` matches.
   - The evidence table row for the KV260 backend still ends "not yet on the board". The new top paragraph covers it ("Most of this page predates…"), but that table is where a judge looks.
   - `:286-288` now reads "nothing in this section has run in FPGA fabric. (That paragraph predates the board run…)", which is awkward. Stating the board result there would read better.

7. **"68 programs" counts files** (`README.md:46`, `summary.md:27`). `firmware/` has 68 `.pasm` files holding 72 `.program` blocks (`spi_master_modes` 4, `spi_master_packed` 2), and the 68 include the parked `swd_probe`. "68 firmware files" is exact.

8. **"Reruns and attacks each claim"** (`README.md:134`) is broader than the verifier's scope, which is the claims of each item sent to it. "Reruns and attacks the claims of each item sent to it" is exact.

9. **The link checker's self-test misses four of my nine mutants.** The checker itself is sound: it caught 16 of 16 planted broken links and raised 0 false alarms in 6 non-link cases. These mutants pass both tests:
   - the outside-the-repository check turned off;
   - HTML `<img>` and `<a>` links not scanned;
   - reference-style links not scanned;
   - duplicate-heading `-1` anchors not generated.

   Today's docs have no such links that would break, so this is a gap in its regression value only. Adding one case each to `test_a_broken_link_is_caught` would close it.

10. **Privacy: the commit metadata isn't covered by a file search.**
    - The four commits' author and committer email is not a GitHub noreply. It is the same single address on all 546 commits up to e449e40, built from the provider machine's hostname. RULES' going-public line names commits explicitly.
    - The files are clean (finding list below), so this is for item AI. The history rewrite must rewrite author and committer identity, not only file contents.
    - Changed files still contain pre-existing `runner1` and `runner1:~/protoemu-flow/...` references: 8 in `eth10-network.md`, none in added lines. They are not home paths or real hostnames, but they are a machine alias a public reader can't follow. They are worth a pass on the tidy branch (item AG).

11. **The layout image's caption** (`README.md:18-23`) says the cores, FIFOs, line unit and host port "fill the rest". At 1200x661 the image shows only the two SRAMs as recognisable blocks. That is fine as a picture, but a judge can't check the caption from it. A labelled overlay, or "standard-cell logic fills the rest", would avoid an unverifiable caption.

## The builder's claims

1. **LINECFG table: holds.**
   - My own decode of each value against `isa.md:271`'s fields matches `line-unit-settings.md:39-41,46` and `README.md:74-77`:

     | Value | Fields |
     |---|---|
     | `0x49F3` | EN, NRZI, K 6, ONES, DIFF, END 1, END_BITS 2, RX |
     | `0x4005` | EN, Manchester, RX |
     | `0x4E85` | EN, Manchester, DIFF, END 2, END_BITS 3, RX |
     | `0x6029` | EN, NRZ, K 5, complement, IDLE, RX, single pin |

   - Every cited line states the value or its fields: `usb_ls_dev.pasm:5`, `usb_fs_dev.pasm:18`, `usb_enum.pasm:12`, `eth10_rx.pasm:2`, `eth10_match.pasm:3`, `eth10_reply.pasm:15`, `usbenum.py:21`, `ethreply.py:26` (END_BITS = 3 at `:25`) and `ethhost.py:35`.
   - The test.py lines are the stated writes, inside the stated tests:

     | Line | Inside |
     |---|---|
     | 1231 | the `usb_autonomous` helper |
     | 1333 | `test_usb_fs_device` |
     | 1499 | `test_eth10_rx_jitter` |
     | 1889 | `test_eth10_rx_streaming` |
     | 1952 | `test_eth10_common_clock_loopback` |
     | 2093 | `test_usb_concurrent_service` |

   - `test_line.py`'s `cfg()` gives exactly 0x49F3, 0x4E85 and 0x6029 for its three cases.
   - The CAN case is 10 zeros, then 11 ones, then 43 random bits (64 in all), received back by the unit itself with no independent decoder. The table's "Model only" label is right.
   - I grepped every LINECFG write in tests and tools. No protocol value is left out of the table; the others are 0 (disable) and lockstep's random values.
2. **"Configured by software": accurate in itself.**
   - LINECFG, LINEDIV and LINEPINS are `W` in `isa.md:737-739` ("Configuration registers are write-only (W: reads return 0)", `:742`). No ISA path lets a core write them.
   - "When it loads a program" describes every tool and test that writes them, although the host could write them at any time.
   - The overclaim is in the surrounding sentences: finding 2.
3. **≥ 13 instructions per bit: holds.** `usb-full-speed.md:160` says so, and my own count of `usb_ls_rx`'s loop gives 13 and 14 (note 3).
4. **68 programs: 68 files** (note 7).
5. **21 post-freeze programs, 16 RTL tests at gate level: holds.**
   - The results table in `post-freeze-protocols.md` lists 21 distinct programs (10 + 5 + 6 by round).
   - Gate level, functional: 16/16. SDF typ: 16/16 after 8dd249a's 1 ns tolerance. Slow and fast: 16/16.
   - All three rounds ran on the KV260: 15/16 (the DShot fix is explained), 25/25 and 31/31.
   - None of the 21 programs changed after the GL run's software, according to `git diff --stat 7685aba..HEAD -- firmware/`.
6. **Protocol table: holds apart from finding 1.**
   - Every G mark maps to one of four sources:
     - the 25 test.py tests at 7685aba (I mapped each test to the firmware it loads at that commit);
     - test_eth_service and test_pio;
     - `test_post_freeze`;
     - the network-stack GL run (`eth10-network.md:35,44-48`).
   - Every `*` matches the seven pre-existing files changed since 7685aba (claim 11).
   - Every F mark has a `fpga/kv260/README.md` row or evidence page, and the blanks for `usb_ls_rx` + `usb_ls_tx` and `usb_uart_*` agree with its "not run" list (`:860-863`, `:946-947`).
   - R blanks (CAN, JTAG, 1-Wire, PS/2, WS2812, `i2c_sniff`, `eth10_match`): no cocotb test loads them.
7. **The clock figures: hold.**
   - +0.010 ns, and hold +0.191 / +0.362 / +0.095 ns: `physical.md:316-317`.
   - 0.83 ns = 20.833 − 20.
   - `guide.md:95-98` now gives both margins, and the slow-corner setup is the thinner, as the builder says.
   - `info.yaml` `clock_hz` is 50000000, unchanged (0 diff lines).
   - Wording: note 4.
8. **Verification counts: hold.**
   - Equivalence: 7429/7429 and 5/5, two engines (`equivalence.md:10-11,179`).
   - Mutants: 34/34 (`verification.md:389`).
   - Gate level: 29 = 25 + 2 + 2 (`release-evidence.md:197`).
   - Verifier reports: 51 files in `evidence/verifier`, 000 to 050. `head -1` of each gives 27 `VERDICT: PASS` and 24 `VERDICT: FAIL`.
9. **"The netlist rebuilds bit for bit": holds.** It is the old README's sentence (`e449e40:README.md:89-91`), and `physical.md:493-497` backs it (fo7 and ci7, the same netlist).
10. **The 141-word firmware: holds.** I assembled with each commit's own `tools/pe` and `usbdev.defines()`:

    | Firmware | At 7685aba | At HEAD |
    |---|---|---|
    | `usb_fs_dev` | 141 | 142 |
    | `usb_enum` | 246 | 253 |
    | `usb_ls_dev` | 129 | 130 |
    | `usb_ls_tx` | 49 | 56 |

    The 141 → 142 change is c4003a2's `got_in` NAK guard.
11. **Seven programs changed: holds.** `git diff --stat 7685aba..HEAD -- firmware/` touches `uart_rx`, `i2c_master`, `can_rx`, `usb_enum`, `usb_fs_dev`, `usb_ls_dev` and `usb_ls_tx`. Every other `.pasm` it lists is new since then (additions only). The other two files it lists are `.timing` files.
12. **The fabric-ping correction: holds**, with a stale table row (note 6).
13. **status.md: a verbatim move.**
    - I word-diffed `e449e40:README.md:25-151` against `docs/status.md`, ignoring link targets, `#` and `**`. The only differences are:
      - the new two-sentence intro;
      - four lead-in colons dropped (the lead-ins became headings);
      - one clock qualifier added: "(+0.010 ns of setup slack there; the demos run at 48 MHz, with margin)".
    - All 21 links resolve to the same targets as before.
    - The old README's tail (from "What we'd do differently from PIO" on) is in the new README unchanged, except the clock qualifier at `:349-350` and the `docs/` row's three new files.
    - The old intro's content is in the new top.
    - No evidence or caveat was dropped.
14. **The layout PNG: holds.**
    - It is 1200x661 and 385,148 bytes.
    - Its tEXt `Cell` is the top module, and its zTXt `Rect` (-32.7, -18.1)-(1321.9, 728.1) µm is centred on fo6's DEF `DIEAREA` 1289.28 x 710.64 µm (centre 644.6, 355.0 against 644.6, 355.3).
    - The GDS on runner1 (read-only) hashes to e67697034da6cd34…, as `physical.md:482` says.
    - The PNG has no other metadata.
15. **The link checker: holds.**
    - 49 files at HEAD, with 490 links, all resolving.
    - The same checker on a `git archive` of e449e40 gives 46 files and 414 links, all resolving.
    - Attack: note 9.
16. **Model suite: holds.** 2880 passed, 29 skipped, rc 0, the builder's figure.

## Asked checks

- **Rules.**
  - No first, only, fastest or "nobody else" in the 632 added lines: every hit is "first" or "only" in a non-superlative sense.
  - Every level is named, and "FPGA is not silicon" is stated (`README.md:10`, `summary.md:7`). Numbers are as checked above.
  - `docs/methodology.md`: 0 diff lines.
  - Exceptions: findings 1 and 2, notes 3 and 4.
- **As a judge.** The top is clear and fits in ten minutes. What I'd change: findings 1 and 2, then notes 3, 4, 5 and 11. The summary's "Limits" section is the most useful paragraph for a sceptical reader. It could sit higher.
- **Privacy.** I searched the 11 changed text files and the PNG's chunks for:
  - home paths, both `/home/` and the dashed form;
  - the provider's name and hostname pattern;
  - tailnet names and `.ts.net`;
  - the user's and author's names;
  - `@`, IPv4 addresses, MACs;
  - SSIDs and Wi-Fi;
  - password, token, secret and key.

  Results:
  - **Added lines:** 0 hits for every pattern. "tail" and "Kria" match only "detail" and "Kria KV260".
  - **Whole files:** the only hits are pre-existing:
    - the public top-module name;
    - the design's own locally administered MACs `02:57:4c:…`;
    - a 10.77.0.2 namespace address;
    - `@posedge` Verilog;
    - `runner1` / `~/` paths (note 10).
  - **PNG:** only the cell name and the view rectangle.
  - **Commit messages:** 0 hits.
  - **Commit identity:** note 10.
- **Mechanical.** In my runner1 clone at 18a5c09, `pytest test/model/test_doc_links.py test/model/test_evidence_index.py` gives "4 passed in 0.22s". Both modules are also inside the model suite runs below.

## What I ran

- **Model suite.** `remote_test.sh model` at 18a5c09 (runner1; `--ignore=test/model/test_swd.py` is built in):
  - rc=0, "2880 passed, 29 skipped in 983.25s (0:16:23)", with "SIGROK CHECKS SKIPPED: 28 tests";
  - a second call, meant for the two modules alone, ran the whole suite again (the wrapper takes suite names, not paths): "2880 passed, 29 skipped in 482.68s (0:08:02)".
- **The two doc tests.** In the runner1 clone at 18a5c09 with the test venv: `pytest -q test/model/test_doc_links.py test/model/test_evidence_index.py` gives "4 passed in 0.22s".
- **Link checker standalone at HEAD.** `python3 test/model/test_doc_links.py`: "49 files: every relative link resolves". In-process count: 49 files, 490 links.
- **Link checker at the base.** The same checker on a `git archive e449e40` (README, docs and every linked tree): "46 files: every relative link resolves", 414 links.
- **Link attack.** `links_attack.py` on runner1 in throwaway copies of an 18a5c09 clone:
  - planted broken links: "caught 16/16";
  - non-links: 6/6 rc 0;
  - checker mutants: 5 of 9 killed. Survivors: no-outside-check, no-html, no-ref, no-dup-suffix.
- **Firmware word counts.** Assembled with each commit's `tools/pe`: `usb_fs_dev` 141 → 142, `usb_ls_dev` 129 → 130, `usb_ls_tx` 49 → 56, `usb_enum` 246 → 253.
- **LINECFG decode** of the four values, plus a `cfg()` check in `test_line.py`.
- **Text diffs.** The status.md word diff (ratio 0.9737; only the differences listed under claim 13), the link-target equality check (21/21) and the old-tail diff.
- **GDS.** `sha256sum` of fo6's GDS and its DEF `DIEAREA` on runner1, read-only.
- **Privacy sweeps.** Counts only, over the changed files, the added lines, the PNG chunks, the commit messages and the commit identities.
