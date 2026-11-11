# 057 present

Branch `present`, commits `29d2799`, `c8a9fbc`, `05db761` and `18a5c09` on `e449e40` (improve-2026-11 after the ocaml-parity2 merge). Backlog item AF.

The author decided on 2026-11-06 to make the repo public: build in public, after a tidy, presentation docs and a history rewrite. This item is the presentation part. It gives the README a top that a judge can read in ten minutes, and a two-page summary. It puts first that the chip is reprogrammed for new protocols after fabrication. An outside reviewer reading the layout had concluded the opposite.

Files changed (12, +632 −155):
- `README.md`;
- new: `docs/summary.md`, `docs/status.md`, `docs/line-unit-settings.md`, `docs/fo6-layout.png`, `test/model/test_doc_links.py`;
- the clock wording in `docs/info.md`, `guide.md`, `verification-story.md`, `explain-wirelingo.md` and `pio-comparison.md`;
- `docs/eth10-network.md`.

The builder's claims to check:

1. **The LINECFG table** (`docs/line-unit-settings.md:39-41,46`).
   - Each value decodes to the fields listed, against `docs/isa.md:271`: `0x49F3` USB low and full speed; `0x4005` 10BASE-T receive; `0x4E85` 10BASE-T receive and reply on one core; `0x6029` CAN-style stuffing after 5.
   - The firmware lines it cites: `usb_ls_dev.pasm:5`, `usb_fs_dev.pasm:18`, `usb_enum.pasm:12`, `eth10_rx.pasm:2`, `eth10_match.pasm:3`, `eth10_reply.pasm:15`, `ethreply.py:26`, `usbenum.py:21`.
   - The tests it cites: `test/test.py` 1231, 1333, 1499, 1889, 1952 and 2093.
   - `0x6029` has no firmware: `can_tx`/`can_rx` do their stuffing in core code, and only `test_line.py` exercises the setting. The table says so. Check the label.
2. **The wording "configured by software".** The builder notes that LINECFG, LINEDIV and LINEPINS are write-only host registers, written over SPI when a program loads, so the README says "configured by software: register writes the host makes when it loads a program". Check this is accurate and doesn't overclaim.
3. **The pure-firmware USB receiver** needs at least 13 instructions per bit (`line-unit-settings.md:13-15`, from `usb-full-speed.md:160`).
4. **68 programs in `firmware/`** (`README.md:47`, `summary.md:30`).
5. **21 post-freeze programs; their 16 RTL tests pass at gate level** (`README.md:61-68`, `summary.md:43-50`).
6. **The protocol table** (`README.md:95-113`). Every mark in the model / RTL / gate level / FPGA columns has evidence behind it:
   - "Gate level" means the 25 main-suite tests at 7685aba, plus the post-freeze and network-stack runs.
   - The `*` marks (an earlier firmware version) follow `git diff --stat 7685aba..HEAD -- firmware/`.
   - Nothing is marked that only a model or an estimate supports.
7. **The clock.**
   - "+0.010 ns" comes from `physical.md:316`; "0.83 ns longer" is the builder's arithmetic (20.833 − 20). See `README.md:150-154`, `summary.md:96-100` and `info.md:18-20`.
   - `guide.md:96` now gives both margins. The fast-corner hold (0.095 ns) was called "the thinnest margin", and the slow-corner setup (+0.010 ns) is thinner.
   - `info.yaml` `clock_hz` stays 50000000 on purpose.
8. **The verification counts** (`README.md:127-135`): 7429/7429, 5/5, 34/34 and 29/29, and 27 PASS / 24 FAIL.
9. **"The netlist rebuilds bit for bit"** (`README.md:158`), copied from the old README.
10. **The usb_fs gate-level run** used the 141-word firmware from before the got_in guard (`summary.md:61-62`).
11. **Seven programs changed since 7685aba** (`summary.md:107-110`).
12. **The fabric-ping correction** (`docs/eth10-network.md:7-11,287-288`). The page said nothing there had run on an FPGA, but the network stack ran on the KV260 on 2026-11-03 (report 037).
13. **`docs/status.md`** is the old README lines 25-151 moved verbatim. Only the links are rewritten for the new path, the bold lead-ins became headings, and one clock qualifier was added. Diff it against `e449e40:README.md` and confirm no evidence or caveat was dropped anywhere in the reorganisation.
14. **`docs/fo6-layout.png`** was rendered with KLayout on runner1 from fo6's GDS, whose hash prefix e67697034da6cd34 matches `physical.md:482`. It is 1200x661 and 385 KB.
15. **The link checker** (`test/model/test_doc_links.py`). 49 files resolve; before these changes it was 46 files and 414 links, with nothing broken. It includes a self-test.
16. **Model suite at 18a5c09:** 2880 passed, 29 skipped, rc 0.

Asked checks:
- **Rules** (RULES.md):
  - no claim of first, only or fastest;
  - each claim at its evidence level, and FPGA not called silicon;
  - every number backed by a test or log;
  - the author's statement in `docs/methodology.md` lines 1-36 unchanged.
- **Read the README top and `summary.md` as a judge would.** Is anything misleading, unclear or overclaimed? Say what you'd change.
- **Privacy:** these files go public. Search every changed file for private strings: hostnames, tailnet names, home paths, emails, SSIDs.
- **Mechanical:** run the two index and link tests (`test_evidence_index.py` and `test_doc_links.py`).

Out of scope:
- `test/gl.log` (75 home paths) is being removed on the tidy branch, item AG.
- The history rewrite comes later, as item AI.

The usual rules: `--ignore=test/model/test_swd.py` on any model run, and no Kria.
