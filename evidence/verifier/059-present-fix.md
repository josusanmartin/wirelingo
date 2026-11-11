# 059 present-fix

Branch `present`, three new commits on `18a5c09` (your 057, FAIL): `857e16c`, `0691ad9` and `9774c41`. The branch base is still `e449e40`.

- `857e16c`: findings 1 and 2, notes 3, 4, 5, 7, 8 and 11.
- `0691ad9`: note 6 (`docs/eth10-network.md`).
- `9774c41`: note 9 (`test/model/test_doc_links.py`).

Note 10 (commit identity) is left for the history rewrite, item AI. The `runner1` mentions stay as they are, and `test/gl.log` is removed on the tidy branch (058).

The builder's claims, keyed to 057:

1. **Finding 1.**
   - `README.md:99`: the SPI row's R cell names `spi_master`, `spi_master_packed` and `spi_master_fast`, and says modes 1-3 are model and FPGA only.
   - `README.md:256`: a new `spi_master_modes` row in the full table. It covers:
     - the model tests: `test_spi_modes`, `test_spi_modes_timing`, all four modes at dividers 3 and 4 and two MISO delays;
     - the KV260 runs: modes 0 and 3 against the SPI device, all four via `la_capture` (`fpga/kv260/README.md:427-428`);
     - the mode-3 limit: right up to 77.5 ns, 1 bit late beyond;
     - no RTL test.
2. **Finding 2: your framing.**
   - It says: no protocol is fixed in the silicon; the cores run firmware; the line unit is a fixed menu built with USB and 10BASE-T in mind (CAN-style stuffing also fits); the host picks from it with three register writes at load; software cannot add a coding.
   - It appears at `README.md:43-61`, `summary.md:25-39`, `line-unit-settings.md:3-15` and `info.md:38-41`.
   - It is reconciled in `explain-wirelingo.md:584-590` and `rules-check.md:122`.
   - None of the four overclaiming phrases appears anywhere in README.md or docs/.
3. **Note 3.** `README.md:59-61`, `summary.md:37-39` and `line-unit-settings.md:21-26` now give the mechanism: both-edge sampling and phase tracking, against one sample per clock for a core. `line-unit-settings.md:24-26` adds:
   - every 10BASE-T receiver in `firmware/` uses the unit;
   - this is the mechanism, not a measured comparison.
4. **Note 4.** `README.md:164-171` and `summary.md:104-108`:
   - The 48 MHz margin is derived (+0.833 ns on every STA row), not measured on fo6.
   - ±13.5 ns is at 50 MHz, and ±12 ns at 40 MHz is from a one-off model sweep (`guide.md`).
5. **Note 5.** The legend at `README.md:92-93` explains "suppl." and the blanks.
   - The G cells show the supplementary runs: `spi_master_packed` (`:99`) and the UART-to-USB row (`:114`).
   - `README.md:118-122` gives `test_usb_uart_rtl` 3/3 and `test_spi_packed_host` 1/1 at validation `61af9b2`.
6. **Note 6.**
   - `eth10-network.md:37` no longer says "not yet on the board".
   - `:38` is a new row with the board result at its level: "at one of its divider phases", report 037.
   - `:286-292` is rewritten, keeping the limit that the hand-back paths ran only on the fake board.
7. **Note 7.** "68 firmware files" at `README.md:47` and `summary.md:26-27`.
8. **Note 8.** "Reruns and attacks the claims of each item sent to it" at `README.md:143` and `summary.md:92`.
9. **Note 9.** `test_a_broken_link_is_caught` now covers:
   - a link out of the repository, to a file that exists;
   - a broken `<img src>` and `<a href>`;
   - a broken reference-style link;
   - a `-2` anchor where only `-1` exists.

   The builder planted your four surviving mutants by hand, and the self-test now kills all four.
10. **Note 11.** The caption at `README.md:22-23` says "standard-cell logic fills the rest".
11. **Checks.**
    - `test_doc_links.py`: 49 files, every link resolves.
    - `test_evidence_index.py`: passes.
    - Model suite at 9774c41 on runner1: 2880 passed, 29 skipped.
    - Privacy grep: no new matches in files or commit messages.

Asked checks:
- Re-check findings 1 and 2 and each note against the diff `18a5c09..9774c41`.
- Confirm that no other place in README.md or docs/ still contradicts the new line-unit framing.
- Rerun your four link-checker mutants.
- Read the README top once more as a judge.

The usual rules: `--ignore=test/model/test_swd.py` on any model run, and no Kria.
