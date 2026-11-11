VERDICT: PASS

Item 061 `present2`: commit 5674e33 on 738ce04, checked out detached. I read the diff `738ce04..5674e33` and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

Both 059 notes are fixed, the change is confined to the stated lines, and each new sentence is true as worded. That includes "no 10BASE-T receiver in firmware alone has been written", which the whole history bears out.

## Findings

None blocking.

### Notes

1. **For a reader who doesn't know the firmware names,** "the pure-firmware receiver (`usb_ls_rx`) … so it cannot keep up" (`README.md:58-60`) doesn't say that `usb_ls_rx` is the low-speed receiver. The sentence is about full speed. "(`usb_ls_rx`, the low-speed one)" would make the step from 13 instructions per bit to 4 clocks per bit explicit. This is cosmetic.
2. **Firmware already decodes Manchester at slow rates.** `rc5_rx` and `dali_ctrl` are post-freeze, firmware-only receivers with no `IN LINE`. So what the line unit buys for 10BASE-T is the rate and the jitter tolerance, not the coding itself. The text doesn't claim otherwise. A judge might still welcome "(Manchester at kHz rates, RC-5 and DALI, is decoded in firmware)" beside the 10BASE-T sentence.

## The builder's claims

1. **The diff is confined: holds.** `git diff --stat 738ce04..5674e33` gives `README.md | 14 ++++++++------` and `docs/summary.md | 11 ++++++-----`. The README hunk is `@@ -54,12 +54,14 @@` and the summary hunk is `@@ -32,11 +32,12 @@`, covering `README.md:57-64` and `summary.md:35-40`. Nothing else changes.
2. **"13 instructions per bit" names its source: holds.** Both files now say "the pure-firmware receiver (`usb_ls_rx`) takes at least 13 instructions per bit", as `line-unit-settings.md:18-19` does. It is exact: in 057 I counted `usb_ls_rx`'s loop at 13 instructions on its one-path and 14 on its zero-path (`firmware/usb_ls_rx.pasm:78-130`).
3. **The 10BASE-T reason is stated as mechanism: holds.**
   - README: "that is the mechanism, not a measured comparison, since no 10BASE-T receiver in firmware alone has been written".
   - summary: "this is the mechanism, not a measured comparison".
   - Both match `line-unit-settings.md:24-26`.
4. **"No 10BASE-T receiver in firmware alone has been written": true.**
   - Every 10BASE-T receive program ever in `firmware/` uses the line unit. I listed them from `git log --all --name-only -- 'firmware/*'`: `eth10_rx`, `eth10_rx_fcs` (the v3 study), `eth10_match` and `eth10_reply`. Every historical version of each contains `IN LINE`.
   - `eth10_rx` was introduced already using it: 37dd63a, "10BASE-T RX through the line unit".
   - Before the unit existed, 878a876 put "10BASE-T RX is not attempted" in the README. Its reasoning was on paper: with a 2-flop synchroniser at 40-50 MHz, edge-timed sampling leaves 0-1 clocks against ±13.5 ns.
   - The "40-MHz receive prototypes" of `eth10-receiver.md:7` are the 40 MHz FPGA builds, which also use the unit (`hardware-day.md:307`).
   - No doc or commit message describes a firmware-only attempt.
5. **Checks: hold.**
   - In a runner1 clone at 5674e33, `pytest test/model/test_doc_links.py test/model/test_evidence_index.py test/model/test_private_hosts.py` gives "7 passed in 0.34s".
   - Locally, `python3 test/model/test_doc_links.py` gives "49 files: every relative link resolves".
   - I ran no model suite. The diff changes only two `.md` files, which no test other than these reads.

## Asked checks

- **As a judge.** The paragraph now separates what is measured (USB's instruction count) from what is argued (10BASE-T's sampling mechanism), and says why the second can't be measured yet. It reads straight. "Two protocols use it.", with the reasons following, is clearer than the old "because" clause. Only notes 1 and 2 are left, both optional.
- **Rules.** The 14 added lines have 0 hits for first, fastest, "nobody", unique or best. Levels are unchanged.
- **Privacy.** 0 hits in the added lines for:
  - `runner1` and `~/`;
  - `/home`;
  - the provider and mail-provider names, the hostname pattern;
  - `.ts.net` and the tailnet-label class;
  - `@`, IPv4;
  - the author's name and the user's domain.

  The commit message has 0 hits and ends with the Co-Authored-By line.

## What I ran

- `git diff 738ce04..5674e33` and its `--stat`.
- `git log --all --name-only -- 'firmware/*'`, plus `git show <rev>:<file> | grep -ci 'in line'` for every version of each 10BASE-T receive file.
- `git show 878a876`.
- `grep` of docs and commit messages for firmware-only Manchester or 10BASE-T receive.
- `grep -ci 'in line'` on `firmware/rc5_rx.pasm` and `firmware/dali_ctrl.pasm`: 0 each.
- The doc tests above, and the privacy counts over the added lines and the commit message.
