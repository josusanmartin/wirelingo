VERDICT: PASS

Item 063 `present3`: commit 82bf76f on 088d703 (improve-2026-11 after the present2 and privacy-needles merges). I read the diff `088d703..82bf76f` and nothing of the builder's reasoning, without needing a checkout: every check below ran on `git show` of the commit or on a throwaway runner1 clone. Kria: verifier does not touch it. No SWD.

Both 061 notes are fixed. Every new fact matches its source and the arithmetic is right. The branch merges cleanly with usb-config-prestage in either order, to the same tree. Two cite slips follow; neither blocks.

## Findings

None blocking.

### Notes

1. **The full-table cite is off by three lines.** The item cites `README.md:261` for the full table naming `usb_ls_rx` as the low-speed receiver. At 82bf76f, `:261` is the `spi_master_modes` row. The `usb_ls_rx` + `usb_ls_tx` row is `:264`. That row already said "low-speed device" before this commit and isn't changed by it.
2. **The RC-5 half-bit cite is the transmitter's row.** The item cites `post-freeze-protocols.md:46` for RC-5's 889 µs half bit. That row is `rc5_tx` ("half bits of 32 periods (888.9 us)"). The receiver's row is `:47`. The half bit is the protocol's, so the figure is right for `rc5_rx` too.

## The builder's claims

1. **`usb_ls_rx` is named as the low-speed receiver: holds.**
   - `README.md:59`: "(`usb_ls_rx`, the low-speed one)".
   - `summary.md:36-37`: "`usb_ls_rx`, the low-speed one".
   - Full table: note 1.
2. **For 10BASE-T, the unit adds rate and jitter tolerance, not the coding: holds.**
   - At 82bf76f, `grep -ci 'in line'` is 0 for both `firmware/rc5_rx.pasm` and `firmware/dali_ctrl.pasm`.
   - Both say they decode bi-phase:
     - `rc5_rx.pasm:9`: "RC-5 is bi-phase (a 1 is a space then a burst…)";
     - `dali_ctrl.pasm:11`: "Bits are bi-phase: a 1 is low then high, a 0 high then low, Te = 416.67 us".
3. **The rates: hold.**
   - DALI: half bit 416.67 µs (`dali_ctrl.pasm:11`), so 1 / (2 × 416.67 µs) = 1200.0 bit/s.
   - RC-5: half bit 888.9 µs (note 2), so 1 / (2 × 889 µs) = 562.4 bit/s.
   - "About 0.56 and 1.2 kbit/s" is right, and the README gives it as an approximation.
4. **Checks: hold.**
   - In a runner1 clone at 82bf76f, `pytest test/model/test_doc_links.py test/model/test_evidence_index.py test/model/test_private_hosts.py` gives "7 passed in 4.32s".
   - The added lines' privacy grep is clean (below).

## Asked checks

- **Only the stated lines changed: yes.** `git diff --stat 088d703..82bf76f` gives `README.md | 11 +++++++----` (+7 −4), `docs/line-unit-settings.md | 5 ++++-` (+4 −1) and `docs/summary.md | 12 +++++++-----` (+7 −5). The hunks are at `README.md:56`, `line-unit-settings.md:23` and `summary.md:33`, matching the stated ranges.
- **Each fact against its source: holds**, with notes 1 and 2.
- **The merge with usb-config-prestage, either order: clean.** In a throwaway runner1 clone, both orders merge onto 088d703 with no conflict and give the same tree, `[host-only tree id redacted]`:
  - 088d703 + 82bf76f, then + ab2db8e;
  - 088d703 + ab2db8e, then + 82bf76f.

  `git merge-tree --write-tree 82bf76f ab2db8e` is clean too. On the merged tree, the three tests above give "7 passed", and the README holds both branches' text.
- **As a judge.** The paragraph now says what the line unit is for in terms a reader can check: slow Manchester in firmware exists (RC-5, DALI), and 10BASE-T's rate and jitter are what need the unit. It reads well. The sentence is long, but each clause carries a fact.
- **Rules and privacy.**
  - The 18 added lines have 0 hits for first, only, fastest, "nobody", unique and best, and none for:
    - `runner1` and `~/`;
    - `/home`;
    - the provider and mail-provider names, the hostname pattern;
    - `.ts.net` and the tailnet-label class;
    - `@`, IPv4;
    - the author's name and the user's domain.
  - The commit message has 0 hits and ends with the Co-Authored-By line.

## What I ran

- `git diff`/`--stat 088d703..82bf76f`, and `git show 82bf76f:<file>` for the firmware comment lines, the `IN LINE` counts, `post-freeze-protocols.md:46-47` and the README line numbers.
- The two rate divisions.
- In a runner1 clone: both merge orders with `git merge`, `git merge-tree --write-tree 82bf76f ab2db8e`, and the three tests at 82bf76f and on the merged tree.
- Privacy and superlative counts over the added lines and the commit message.
