VERDICT: PASS

Item 059 `present-fix`: commits 857e16c, 0691ad9 and 9774c41 on 18a5c09 (branch `present`, base e449e40), checked out detached at 9774c41. I read the diff `18a5c09..9774c41` (8 files, +112 −63) and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

057's two blocking findings are fixed, and so are notes 3-9 and 11. Note 10 is deferred to item AI, as the item says. Nothing in README.md or docs/ still contradicts the new line-unit framing. All nine of my link-checker mutants are now killed. Two small wording notes follow; neither blocks.

## Findings

None blocking.

### Notes

1. **"A firmware receiver needs at least 13 instructions per bit"** (`README.md:59`, `summary.md:36-37`) generalizes from one program. The 13 is `usb_ls_rx`'s count: 13 on its one-path and 14 on its zero-path, which I recounted in 057. `line-unit-settings.md:18-19` says it exactly: "The pure-firmware receiver takes…". Suggested wording: "our pure-firmware receiver (`usb_ls_rx`) takes at least 13".
2. **The README still says flatly that for 10BASE-T receive "a core alone can't keep up"** (`README.md:57-60`, `summary.md:35-38`). Only `line-unit-settings.md:24-26` adds that it "is the mechanism, not a measured comparison". The mechanism given (both-edge sampling and phase tracking against one sample per clock) is correct and now stated. Carrying the "not measured" clause, or "is built for", into the README sentence would match the settings page.

## 057's findings and notes

1. **Finding 1 (SPI R mark): fixed.**
   - `README.md:99`: R names `spi_master`, `spi_master_packed` and `spi_master_fast`, with "(modes 1-3: model and FPGA only)".
   - New row `README.md:256`, checked against its sources:
     - "CPOL by a define, CPHA by program block": `firmware/spi_master_modes.pasm:2-3` ("Assemble BOTH blocks with CPOL = mode >> 1; select block mode & 1").
     - Model coverage: `test_spi_modes.py:92-95` (4 modes × dividers 3, 4 × delays 1, 2), `:118` (TX starvation), `:136` (RX backpressure), `:160` (reload in another mode), and `test_spi_modes_timing.py`. Its docstring says "independent peer samples actual SCK edges".
     - KV260: `fpga/kv260/README.md:427` ("Mode 3 at CLKDIV 3: right up to DELAY 29 (77.5 ns), 1 bit late at 30-31 … 4 clocks = 80 ns") and `:428` (all four modes by `la_capture`).
     - "no RTL test": confirmed in 057.
   - G now adds `spi_master_packed` as suppl.
2. **Finding 2 (line-unit framing): fixed and reconciled.**
   - The new text is at `README.md:42-61`, `summary.md:25-39`, `line-unit-settings.md:3-15` and `info.md:38-41`.
   - The two contradicting places now agree:
     - `explain-wirelingo.md:584-590`: "holds fully for the cores; for the line unit it holds only as far as the menu reaches";
     - `rules-check.md:122`.
   - The frame endings listed, "release, SE0, high, hold", are isa.md §4.7's END 0-3.
   - Search of README.md and docs/*.md for any remaining contradiction: these phrases have 0 hits: `protocol-specific`, `no protocol built`, `nothing protocol`, `everything protocol`, `nothing in it is specific`, `configured by software`, `protocol-agnostic`, `fully programmable`, `purely software`. "too slow for" has 1 hit, an unrelated RMII clock sentence (`rmii-study.md:199`). I also read every "line unit" sentence for "generic", "any", "universal" and "agnostic": no contradicting claim.
3. **Note 3 (10BASE-T reason): fixed** in substance (see note 2 above).
   - `line-unit-settings.md:21-26` gives the mechanism and says it isn't measured.
   - "Every 10BASE-T receiver in `firmware/` uses the unit" is right. `eth10_rx`, `eth10_match` and `eth10_reply` all contain `IN LINE`. The other matches for 10BASE or Manchester are transmitters (`eth10_tx`, `eth10_tx_cs`) or RMII (`rmii_match`).
4. **Note 4 (derived 48 MHz margin; 50 vs 40 MHz jitter): fixed.**
   - I parsed all 21 rows of `physical.md`'s settings table (`:233-253`) that give slack at both clocks. In every one, the 48 MHz slack minus the 50 MHz slack is exactly 0.833 ns (0 rows off), as `README.md:164-167` now says.
   - The ±13.5 ns at 50 MHz and ±12 ns at 40 MHz "in a one-off model sweep" match `guide.md:99`. The link anchor `#known-limits` resolves.
5. **Note 5 (suppl. gate level): fixed.**
   - The legend (`README.md:92-93`) defines "Suppl." and says a blank means no test at that level. I checked the blanks against 057's per-firmware mapping, and none contradicts it.
   - "the same three, suppl." for UART-to-USB, and `spi_master_packed` suppl., match `release-evidence.md:240-245` (`test_usb_uart_rtl` 3/3: the three in the R cell; `test_spi_packed_host` 1/1; validation `61af9b2`).
6. **Note 6 (eth10-network): fixed.**
   - `:37` no longer says "not yet on the board".
   - The new row `:38` matches `evidence/fabric-ping-20261103/README.md:33,37-46` claim for claim:
     - `--probe`: ARP, ping and UDP each answered with a 64-byte reply, FCS right; a bad FCS and another MAC get no reply;
     - `--compare`: equal "at one of its divider phases";
     - `--selftest`: 5 of 5 pings, UDP echo, no reply for an unknown IP, another IP, another MAC or a bad FCS;
     - no cable, PMOD pin or pads.
   - `:286-293` is now stated at its level and keeps the limit that the hand-back paths ran only on the fake board.
7. **Note 7: fixed.** "68 firmware files" at `README.md:46-47` and `summary.md:26-27`. No "68 programs" is left in README.md or docs/.
8. **Note 8: fixed.** `README.md:143` and `summary.md:92`. No "attacks each claim" is left.
9. **Note 9 (link checker self-test): fixed.** The self-test now plants:
   - a link to an existing file outside the root;
   - broken `<img src>` and `<a href>`;
   - a broken reference-style link;
   - `#again-2` where only `-1` exists, with `#again-1` in the good set.

   My rerun of all nine mutants from 057 in a throwaway runner1 copy kills all nine. The four former survivors (no-outside-check, no-html, no-ref, no-dup-suffix) are now `KILLED`. Each fails one test and passes the other. The docs hold no broken HTML or reference link for the whole-docs test to trip on, so the failing test is the self-test. The 16 planted bad links are still all caught, and the 6 non-link cases still raise no alarm.
10. **Note 10 (commit identity):** deferred to item AI, as stated. These three commits carry the same identity as before. Their messages have 0 private-string hits, and each ends with the Co-Authored-By line (3 of 3).
11. **Note 11 (caption): fixed.** "standard-cell logic fills the rest" (`README.md:22-23`).

## Asked checks

- **As a judge.** The top now says plainly what is fixed (the line unit's menu) and what is chosen after fabrication (the program and the menu settings), with no claim the repo contradicts. The SPI row and its new full-table row tell a reader where each mode's evidence stops. The clock paragraph says which numbers are derived. I'd make the two wording changes above and leave the rest.
- **Rules.**
  - No first, only or fastest in the 112 added lines: every "first" or "only" is non-superlative.
  - Levels are stated, and FPGA is not called silicon.
  - `docs/methodology.md`: 0 diff lines.
- **Privacy.** 0 hits in the 112 added lines for:
  - `runner1` and `~/`;
  - `/home`;
  - the provider and mail-provider names, the hostname pattern;
  - `.ts.net` and the tailnet label;
  - `@`, IPv4, MAC;
  - SSID;
  - the author's name and the user's domain.

  The commit messages are clean too.

## What I ran

- **Model suite.** `remote_test.sh model` at 9774c41 (runner1; `--ignore=test/model/test_swd.py` is built in): rc=0, "2880 passed, 29 skipped in 536.96s (0:08:56)", with "SIGROK CHECKS SKIPPED: 28 tests".
- **The two doc tests.** In a runner1 clone at 9774c41: `pytest -q test/model/test_doc_links.py test/model/test_evidence_index.py` gives "4 passed in 0.23s". Locally, `python3 test/model/test_doc_links.py` gives "49 files: every relative link resolves".
- **Link attack.** 057's `links_attack.py` on runner1 at 9774c41: "caught 16/16"; non-links 6/6 rc 0; checker mutants 9 of 9 `KILLED`.
- **Slack check.** A parse of `physical.md`'s settings table: "rows 21 off 0".
- **Searches.** The phrase searches above over README.md and docs/*.md, the `IN LINE` count per 10BASE-T firmware, and the privacy counts over the added lines and the commit messages.
