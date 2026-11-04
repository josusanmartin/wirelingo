VERDICT: FAIL

Commit a85fc57 on base e28a26b (an ancestor), one commit, 23 files.
- **Added:** the 18 files 030-038 in `evidence/verifier/`.
- **Changed:** its README; `README.md`, `evidence/README.md` and `evidence/fabric-ping-20261103/README.md`; 7 lines of `docs/eth10-network.md`.
- **Unchanged:** nothing in `src/`, `flow/`, `tools/`, `firmware/` or `test/`.

The redactions are complete, the 14 unredacted files are byte-identical, and the verdict table and its counts are right. One number in the redaction record is wrong, and claim 1 repeats it.

| Claim | Result |
|---|---|
| 1. 18 files copied; 14 identical, 4 redacted; "17 replacements on 16 lines"; each listed by file, line and kind; sha256 of all 18 sources and copies | **FAIL on the line count** (finding 1); everything else in it PASS |
| 2. Kept on purpose, with reasons; no SSIDs or credentials; 000-029 re-checked for the new kinds | PASS |
| 3. Rows 030-038; 39 items, 39 reports, 19 PASS, 20 FAIL, 2026-11-02 to 2026-11-04 | PASS |
| 4. fabric-ping README links 031, 034, 037; top-level and evidence READMEs updated; eth10-network citations linked | PASS |
| 5. Model suite 2455 passed, 29 skipped; 155 links, 0 broken; no tailnet label or build hostname | PASS |

## Findings

1. **"17 replacements on 16 lines" is 17 on 15** (`evidence/verifier/README.md`, the sentence after the redaction table).
   - **The README's own table has 15 rows**, one per line:
     - 030 report: `:121`;
     - 035 item: `:4`, `:6`, `:9`;
     - 035 report: `:8`, `:12`, `:14`, `:23`, `:54`, `:62`, `:63`, `:67`, `:68`, `:69`;
     - 036 report: `:111`.
   - **The diff agrees.** `diff` of each committed file against its inbox source gives 1 + 3 + 10 + 1 = 15 changed lines.
   - **So does a count of the placeholders.** The bracketed notes come to 17, with two each on 035 item `:9` and 035 report `:14`.
   - **Where the 16 comes from.** Adding up the summary table's per-kind counts gives it: 7 tailnet + 4 home directory + 2 hostname + 2 email + 1 MAC = 16. That counts 035 item `:9` twice, since it holds both a home directory and a hostname.
   - **Why it blocks.** It is a one-word fix, but the record exists so that a reader can check the redactions by counting. A count that doesn't match its own table is the kind of thing this folder must not carry, and the item's claim 1 states the same wrong number.

## Checked

**The asked check: are the redactions complete?**
- **The originals' values, searched in the whole tree at a85fc57** (`git grep -i -F`, binaries included). Each value was taken from the inbox sources, and only counts were printed:
  - the Kria's eth0 MAC with colons, with dashes and bare, and its NIC half alone: 0 files;
  - the tailnet label: 0;
  - the build machine's hostname and provider domain: 0;
  - the author's address: 0;
  - the local home directory: only `test/gl.log`, the known unfixed sweep item from 035.
- **The 18 committed files, by pattern:**
  - emails: only the 7 kept ones the README lists (`ubuntu@kria.example` ×8, `ubuntu@kria.local`, `root@example.org`, `n@cocotb.test`, `modprobe@loop.service`, `forum@t13.org`, `asic-competition@janestreet.com`);
  - MACs: only `02:00:de:ad:be:01`, plus the truncated `02:57:4c:…` (both locally administered);
  - IPv4: only `100.64.0.0`, as a class searched for;
  - `ts.net`: only as a class, or in my own elided `TAIL…TS.NET`;
  - `ssid` and `psk`: only as words in the sweep lists;
  - `/home/`: only `/home/ubuntu`, on the board.
- **000-029, for the new kinds:** no MAC, no email, no SSID or `psk`, and no home directory other than the example `/home/pi` (8) and `/home/someone` (1). As the README says.

**The asked check: are the 14 unredacted files byte-identical?**
- `cmp` of each committed file against my inbox: 14 identical. The four that differ are the four listed.
- Every changed line holds a bracketed note in the place of the redacted string, and nothing else on the line changed (`diff`, read line by line).
- **All 36 sha256 values** in the README match: the inbox column against my inbox now, and the committed column against `git show a85fc57:…`.

**The asked check: the counts in the table.**
- The 39 rows 000-038 each have the item's name. Each commit hash appears in its item.
- Each verdict equals the first line of the report in my inbox.
- 19 PASS and 20 FAIL over 000-038; 39 items and 39 reports.
- I did not check the dates (2026-11-02 to 2026-11-04).
- "Items 039 and 040 have no report yet" was true at the commit; 039 and 040 have reports now.

**Claim 4, and over-claiming.**
- The new links in the fabric-ping, top-level and evidence READMEs, and `docs/eth10-network.md:385-389`, all resolve.
- "Fabric ping … verified ([report 037])" is what 037 says. The fabric-ping README is clear that the 2026-11-03 board file is from `da82a51` and was compared SAME against a `1b3ff6b` model file, and that `smoke-1b3ff6b/` is the later run of the final code.
- "What the verifier is" in the README describes this session accurately (read-only checkout, runner1 scratch, never the Kria).

**Claim 5.**
- `remote_test.sh model` at a85fc57: `2455 passed, 29 skipped`. That is the same count as e28a26b, as expected for a docs-and-evidence commit.
- **My link check** over the 5 changed `.md` files (relative targets, with `#anchors` checked against headings): `155 relative links, 0 broken`.

## Notes

1. The README's "copied … at about 04:55 UTC" can't be checked from the repo. The sha256 column pins the content, which is what matters.
2. To keep the record in step, adding 039-041 in the same way will need this README's counts and the two top-level counts updated together.

## What I ran

**On runner1:** `remote_test.sh model` at a85fc57: `model rc=0`, `2455 passed, 29 skipped in 356.93s (0:05:56)` (checkout `agent-runs/protoemu-verify-a85fc57-1823968`, removed).

**Locally** (no runner needed):
- `cmp` and `diff` of the 18 files against my inbox;
- the 36 sha256 values;
- the value searches over the whole tree at a85fc57, printing counts only;
- the pattern sweeps over 000-038;
- the placeholder count per line;
- the verdict, name and commit check of the 39 rows;
- the link check over the five changed docs, on a `git archive` copy in my scratchpad.

I did not touch the Kria or SWD, and nothing was pushed.
