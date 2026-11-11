# 051 verifier-reports3

Branch `verifier-reports3`, commits `8f7fad1` and `fff2b3f`, on `d7f177d` (improve-2026-11, after the usb-in-setup merge). Backlog item AB, a repeat of W (items 041/042).

The builder copied inbox items and reports 042-050 into `evidence/verifier/`, and updated that folder's README and the coverage lines elsewhere. Check the following claims.

1. **Copies.** 18 files were added, an item `.md` and a `.report.md` for each of 042-050. Each one is byte-identical to `~/dev/protoemu-verify-reports/inbox/`, except on the redaction lines. The README gives each file's sha256 in the inbox and as committed.
2. **Redactions.** There are exactly 2, both `[tailnet domain redacted]`: `042-verifier-reports2-fix.report.md:42` and `043-fabric-ping3.report.md:24`.
   - **Deliberate inconsistency:** files 030, 035, 038 and 041 keep the same kind of class-name mention unredacted. The README says so.
   - **Nothing else needed redacting:** emails, the provider hostname, `[home directory redacted]`, the Kria eth0 MAC in any form, IP addresses, SSIDs, credentials. The README lists what was kept and why.
3. **Links.** None needed converting: 042-050 have no Markdown links outside code spans.
4. **Counts.**
   - 51 items, 51 reports, 27 PASS, 24 FAIL, 2026-11-02 to 050 (2026-11-04).
   - Items are the `NNN-*.md` files that aren't `.report.md`. A plain `grep -v report` miscounts them, because two item names contain "report".
   - Verdicts are counted from each report's first line.
5. **Unreachable commits.** The README lists six commits that reports name but that are not in this branch's history: `a85fc57`, `e158fe0`, `dc51cc8`, `8fdda85`, `60b0692`, `d1aa46f`. It says what each became, or where it lives. The list is claimed complete for 000-050.
6. **Coverage lines** (`fff2b3f`):
   - `README.md:50`, and `evidence/README.md:9`, `:14`, `:15` and `:30`, now say 000-050 / after 050.
   - No other line in the tree describes this folder's coverage with a stale count. `docs/rules-check.md:154` is left alone, because it is a dated audit quote.
7. **Checks.**
   - `test/model/test_evidence_index.py` passes.
   - The private-string grep finds nothing in the added lines.
   - `remote_test.sh model` at `8f7fad1`: 2867 passed, 29 skipped. `fff2b3f` changes no test.

Asked checks:
- `cmp` every copy against the inbox, and diff the two redacted ones.
- Recount the verdicts yourself.
- Check the unreachable list by checking every hex string in 042-050 against `git rev-list fff2b3f`.
- Note that 042-050 reports quote file paths from your own scratchpad (`[scratch path redacted]/...`). Say whether any of that, or anything else in them, should have been redacted.

As always, add `--ignore=test/model/test_swd.py` to any model run.
