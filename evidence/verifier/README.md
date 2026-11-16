# Verifier reports

Every report of the project's independent verifier, with the item it
answered, from the first (2026-11-02) to item 078b (2026-11-11): 83 items, 83
reports, 52 `VERDICT: PASS` and 31 `VERDICT: FAIL`. FAILs are kept: they are
most of what the verifier caught. (Counted from the files, not from this
text: 83 `NNN-name.md` files that are not `.report.md`, 83 `NNN-name.report.md`
files, and `head -qn1 *.report.md | sort | uniq -c`, which gives 52 `VERDICT:
PASS` and 31 `VERDICT: FAIL` and no other first line. A number with a letter,
such as 069b, is a further round of the same item.)

## What the verifier is

A separate Claude Code session, independent of the builders. It reads the
project read-only, through its own checkout of this repository, in which it
checks out the commit under review; it changes nothing in the project and
writes only its reports. It runs tests on runner1, the compute runner, in its
own scratch directories. It never touches the Kria board. It is told to
assume the builder's claims are wrong until it has seen the evidence, and to
read the code, tests and docs rather than the builder's reasoning. It reruns
the tests itself, and attacks them: it breaks the firmware or the input in a
throwaway copy and checks that a test fails.

The process, and what it does not cover (the verifier is also an agent; it
cannot rerun the FPGA), is in
[docs/verification-story.md](../../docs/verification-story.md#how-an-agents-claim-gets-checked).

## The files

For each number NNN:

* `NNN-name.md` is the **item**: the commit, its base, and the numbered claims
  the verifier was asked to check. Items are written by the orchestrator (the
  main session that assigns the work) from the builder's report. A claim in an
  item is a claim to be checked, not a result.
* `NNN-name.report.md` is the **report**. Its first line is the verdict. Then
  come numbered findings with file:line, how each was shown, and what the
  verifier ran.

Commits named here are local commits of the project. (For 051-078b, which
partly predate the history rewrite of 2026-11-07, see
[051-078b](#051-078b) below.) Every hexadecimal string
of 7 or more characters in the items and reports 000-050 was looked up with
`git cat-file --batch-check` and, where it is a commit, against `git rev-list
HEAD`. All the commits are in this branch's history except these, which name
real commits of the repository that this branch does not contain:

| Commit | Named in | What it is |
|---|---|---|
| `a85fc57` | item and report 041; item 042, report 042 | this folder's update to 038 before it was rebased onto `c5ee980`; the same change, with report 041's finding fixed, is in this branch's history as `2bc09d9` and `1cc1446` |
| `e158fe0` | item and report 048; item 049, report 049 | the `usb-guard2` commit before it was rebased onto `084b699`; `git range-diff` gives it equal to `0388de3`, which is in this branch's history (report 049) |
| `dc51cc8` | report 047 | the `usb-ls-restage` test commit before the rebase onto `87d72ae`; the evidence README of `model-usb-ls-restage-20261104/` maps it to `e59e368`, in this branch's history |
| `8fdda85` | report 047 | the `usb_ls_tx` fix before the same rebase; mapped to `ee15b1a`, in this branch's history |
| `60b0692` | items 032 and 036, reports 032, 036 and 038; item 042, report 042 | the RTL commit the KV260 app `protoemu48` was built from (2026-10-07, on the `kv260-usb` branch only), named as a bitstream's source, not as a commit under review |
| `d1aa46f` | items 029, 031 and 037, reports 001, 029, 031 and 037 | the RTL commit of the KV260 `pe_lab` build (2026-10-06, on no branch), named in the same way |

The other hexadecimal strings in 042-050 are not commits: sha256 prefixes of
files (`2d1a8e27…`, `5a7b9b36…`, `69982302…`, `7d6a1459…`, `80aa7f1b…`,
`8734c99b…`, `b966db0a…`, `f5d30a14…`, `f8293aa0…`), a builder agent's id
and numeric suffix in the path of its checkout that report 043 quotes
(`afe09457da93fe81d`, `1550601`), dates, and the numeric suffixes of the
verifier's own checkouts.

| # | Item | Commit | Verdict | | |
|---|---|---|---|---|---|
| 000 | `eth10-match` | `af2c097` | FAIL | [item](000-eth10-match.md) | [report](000-eth10-match.report.md) |
| 001 | `eth10-match-fix` | `8ae633f` | FAIL | [item](001-eth10-match-fix.md) | [report](001-eth10-match-fix.report.md) |
| 002 | `rmii-study` | `177af7b` | FAIL | [item](002-rmii-study.md) | [report](002-rmii-study.report.md) |
| 003 | `eth-stack` | `3303b45` | FAIL | [item](003-eth-stack.md) | [report](003-eth-stack.report.md) |
| 004 | `eth10-match-fix2` | `84e853c` | FAIL | [item](004-eth10-match-fix2.md) | [report](004-eth10-match-fix2.report.md) |
| 005 | `explain-docs` | `6903fd9` | PASS | [item](005-explain-docs.md) | [report](005-explain-docs.report.md) |
| 006 | `ocaml-port` | `1815536` | FAIL | [item](006-ocaml-port.md) | [report](006-ocaml-port.report.md) |
| 007 | `rmii-fix` | `c3d9f85` | PASS | [item](007-rmii-fix.md) | [report](007-rmii-fix.report.md) |
| 008 | `bench-fw` | `f3ac455` | PASS | [item](008-bench-fw.md) | [report](008-bench-fw.report.md) |
| 009 | `eth10-match-fix3` | `86abad7` | PASS | [item](009-eth10-match-fix3.md) | [report](009-eth10-match-fix3.report.md) |
| 010 | `eth-stack-fix` | `cb0283f` | FAIL | [item](010-eth-stack-fix.md) | [report](010-eth-stack-fix.report.md) |
| 011 | `bench-fw-notes` | `d906eb6` | PASS | [item](011-bench-fw-notes.md) | [report](011-bench-fw-notes.report.md) |
| 012 | `blind-test` | `897be38` | FAIL | [item](012-blind-test.md) | [report](012-blind-test.report.md) |
| 013 | `ocaml-fix` | `d614d46` | PASS | [item](013-ocaml-fix.md) | [report](013-ocaml-fix.report.md) |
| 014 | `faults-pulse` | `26ab95e` | PASS | [item](014-faults-pulse.md) | [report](014-faults-pulse.report.md) |
| 015 | `eth-stack-fix2` | `02a99de` | PASS | [item](015-eth-stack-fix2.md) | [report](015-eth-stack-fix2.report.md) |
| 016 | `hw-kit` | `f31bafe` | FAIL | [item](016-hw-kit.md) | [report](016-hw-kit.report.md) |
| 017 | `ocaml-notes` | `e458a04` | FAIL | [item](017-ocaml-notes.md) | [report](017-ocaml-notes.report.md) |
| 018 | `blind-fix` | `abdc597` | PASS | [item](018-blind-fix.md) | [report](018-blind-fix.report.md) |
| 019 | `fabric-guard` | `d2c4bff` | FAIL | [item](019-fabric-guard.md) | [report](019-fabric-guard.report.md) |
| 020 | `hw-kit-fix` | `00397a5` | PASS | [item](020-hw-kit-fix.md) | [report](020-hw-kit-fix.report.md) |
| 021 | `ocaml-selfcheck` | `c0e4700` | PASS | [item](021-ocaml-selfcheck.md) | [report](021-ocaml-selfcheck.report.md) |
| 022 | `guard-fix` | `539b870` | PASS | [item](022-guard-fix.md) | [report](022-guard-fix.report.md) |
| 023 | `fault-fixes` | `6839e10` | FAIL | [item](023-fault-fixes.md) | [report](023-fault-fixes.report.md) |
| 024 | `rules-check` | `7ebb95e` | FAIL | [item](024-rules-check.md) | [report](024-rules-check.report.md) |
| 025 | `reaction` | `ac44c31` | PASS | [item](025-reaction.md) | [report](025-reaction.report.md) |
| 026 | `rules-check-fix` | `c1459ff` | PASS | [item](026-rules-check-fix.md) | [report](026-rules-check-fix.report.md) |
| 027 | `fault-fixes-fix` | `d14e5a5` | FAIL | [item](027-fault-fixes-fix.md) | [report](027-fault-fixes-fix.report.md) |
| 028 | `fault-fixes-labels` | `74c47a1` | PASS | [item](028-fault-fixes-labels.md) | [report](028-fault-fixes-labels.report.md) |
| 029 | `fabric-ping` | `0a1b76d` | FAIL | [item](029-fabric-ping.md) | [report](029-fabric-ping.report.md) |
| 030 | `docs-fix` | `d60c36d` | FAIL | [item](030-docs-fix.md) | [report](030-docs-fix.report.md) |
| 031 | `fabric-ping-fix` | `8ea1baf` | FAIL | [item](031-fabric-ping-fix.md) | [report](031-fabric-ping-fix.report.md) |
| 032 | `usbfs-zlp` | `06b0dd4` | FAIL | [item](032-usbfs-zlp.md) | [report](032-usbfs-zlp.report.md) |
| 033 | `docs-fix-fix` | `bc39be5` | PASS | [item](033-docs-fix-fix.md) | [report](033-docs-fix-fix.report.md) |
| 034 | `fabric-ping-fix2` | `00e61ad` | FAIL | [item](034-fabric-ping-fix2.md) | [report](034-fabric-ping-fix2.report.md) |
| 035 | `hostname-fix` | `d3ed2e3` | PASS | [item](035-hostname-fix.md) | [report](035-hostname-fix.report.md) |
| 036 | `usbfs-zlp-fix` | `cdd21e0` | FAIL | [item](036-usbfs-zlp-fix.md) | [report](036-usbfs-zlp-fix.report.md) |
| 037 | `fabric-ping-fix3` | `956184e` | PASS | [item](037-fabric-ping-fix3.md) | [report](037-fabric-ping-fix3.report.md) |
| 038 | `hostname-fix2` | `5851b41` | PASS | [item](038-hostname-fix2.md) | [report](038-hostname-fix2.report.md) |
| 039 | `fabric-ping2` | `7d18b14` | PASS | [item](039-fabric-ping2.md) | [report](039-fabric-ping2.report.md) |
| 040 | `usbfs-zlp-fix2` | `333a64b` | PASS | [item](040-usbfs-zlp-fix2.md) | [report](040-usbfs-zlp-fix2.report.md) |
| 041 | `verifier-reports2` | `a85fc57` | FAIL | [item](041-verifier-reports2.md) | [report](041-verifier-reports2.report.md) |
| 042 | `verifier-reports2-fix` | `1d9a4da` | PASS | [item](042-verifier-reports2-fix.md) | [report](042-verifier-reports2-fix.report.md) |
| 043 | `fabric-ping3` | `f3e4744` | PASS | [item](043-fabric-ping3.md) | [report](043-fabric-ping3.report.md) |
| 044 | `usb-guard` | `0c8c8da` | FAIL | [item](044-usb-guard.md) | [report](044-usb-guard.report.md) |
| 045 | `usb-guard-fix` | `8a1f900` | FAIL | [item](045-usb-guard-fix.md) | [report](045-usb-guard-fix.report.md) |
| 046 | `usb-guard-fix2` | `87d72ae` | PASS | [item](046-usb-guard-fix2.md) | [report](046-usb-guard-fix2.report.md) |
| 047 | `usb-ls-restage` | `1b20982` | PASS | [item](047-usb-ls-restage.md) | [report](047-usb-ls-restage.report.md) |
| 048 | `usb-guard2` | `e158fe0` | FAIL | [item](048-usb-guard2.md) | [report](048-usb-guard2.report.md) |
| 049 | `usb-guard2-fix` | `c0cf7f0` | PASS | [item](049-usb-guard2-fix.md) | [report](049-usb-guard2-fix.report.md) |
| 050 | `usb-in-setup` | `d4f5a61` | PASS | [item](050-usb-in-setup.md) | [report](050-usb-in-setup.report.md) |
| 051 | `verifier-reports3` | `fff2b3f` | FAIL | [item](051-verifier-reports3.md) | [report](051-verifier-reports3.report.md) |
| 052 | `demo-notes` | `2a5677d` | PASS | [item](052-demo-notes.md) | [report](052-demo-notes.report.md) |
| 053 | `verifier-reports3-fix` | `f28e573` | PASS | [item](053-verifier-reports3-fix.md) | [report](053-verifier-reports3-fix.report.md) |
| 054 | `reattach-wiring` | `cb93530` | PASS | [item](054-reattach-wiring.md) | [report](054-reattach-wiring.report.md) |
| 055 | `reap-stop` | `0d84715` | PASS | [item](055-reap-stop.md) | [report](055-reap-stop.report.md) |
| 056 | `ocaml-parity2` | `6219a50` | PASS | [item](056-ocaml-parity2.md) | [report](056-ocaml-parity2.report.md) |
| 057 | `present` | `18a5c09` | FAIL | [item](057-present.md) | [report](057-present.report.md) |
| 058 | `tidy` | `bbf8b11` | PASS | [item](058-tidy.md) | [report](058-tidy.report.md) |
| 059 | `present-fix` | `9774c41` | PASS | [item](059-present-fix.md) | [report](059-present-fix.report.md) |
| 060 | `privacy-needles` | `6b78b89` | PASS | [item](060-privacy-needles.md) | [report](060-privacy-needles.report.md) |
| 061 | `present2` | `5674e33` | PASS | [item](061-present2.md) | [report](061-present2.report.md) |
| 062 | `usb-config-prestage` | `ab2db8e` | PASS | [item](062-usb-config-prestage.md) | [report](062-usb-config-prestage.report.md) |
| 063 | `present3` | `82bf76f` | PASS | [item](063-present3.md) | [report](063-present3.report.md) |
| 064 | `privacy-needles2` | `251b323` | PASS | [item](064-privacy-needles2.md) | [report](064-privacy-needles2.report.md) |
| 065 | `public-history` | round 1, unpublished | FAIL | [item](065-public-history.md) | [report](065-public-history.report.md) |
| 066 | `public-history2` | `f7923d4` | PASS | [item](066-public-history2.md) | [report](066-public-history2.report.md) |
| 067 | `public-followups` | `c110c44` | PASS | [item](067-public-followups.md) | [report](067-public-followups.report.md) |
| 068 | `public-followups2` | `aa958a7` | PASS | [item](068-public-followups2.md) | [report](068-public-followups2.report.md) |
| 069 | `8x4-study` | `65d0e8e` | PASS | [item](069-8x4-study.md) | [report](069-8x4-study.report.md) |
| 069b | `8x4-study-fix` | `a8b6e2a` | FAIL | [item](069b-8x4-study-fix.md) | [report](069b-8x4-study-fix.report.md) |
| 069c | `8x4-study-fix2` | `976fed1` | FAIL | [item](069c-8x4-study-fix2.md) | [report](069c-8x4-study-fix2.report.md) |
| 069d | `8x4-study-fix3` | `d2ed344` | PASS | [item](069d-8x4-study-fix3.md) | [report](069d-8x4-study-fix3.report.md) |
| 070 | `pkg3-spec-model` | `effd086` | PASS | [item](070-pkg3-spec-model.md) | [report](070-pkg3-spec-model.report.md) |
| 071 | `pkg3-rtl` | `3a7feab` | PASS | [item](071-pkg3-rtl.md) | [report](071-pkg3-rtl.report.md) |
| 072 | `pkg3-firmware` | `3a7feab` | FAIL | [item](072-pkg3-firmware.md) | [report](072-pkg3-firmware.report.md) |
| 073 | `flow-8x4` | `72e2ccb` | PASS | [item](073-flow-8x4.md) | [report](073-flow-8x4.report.md) |
| 074 | `pkg3-fix` | `eb63aa8` | PASS | [item](074-pkg3-fix.md) | [report](074-pkg3-fix.report.md) |
| 075 | `merge-ao-ap` | `d973462` | PASS | [item](075-merge-ao-ap.md) | [report](075-merge-ao-ap.report.md) |
| 076 | `kv260-3core` | `b3a019a` | PASS | [item](076-kv260-3core.md) | [report](076-kv260-3core.report.md) |
| 077 | `kv260-3core-fix` | `36c6c26` | PASS | [item](077-kv260-3core-fix.md) | [report](077-kv260-3core-fix.report.md) |
| 078 | `home-path-scan` | `2f63d7f` | FAIL | [item](078-home-path-scan.md) | [report](078-home-path-scan.report.md) |
| 078b | `home-path-fix` | `965a520` | PASS | [item](078b-home-path-fix.md) | [report](078b-home-path-fix.report.md) |

## Verbatim, and the redactions

For publication, the history rewrite of 2026-11-07 also redacted one word, the build
host's provider name, in `030-docs-fix.md`, `035-hostname-fix.md`, `035-hostname-fix.report.md`, `041-verifier-reports2.md` and `042-verifier-reports2-fix.report.md`. The "sha256 committed" digests below for those
5 files are of the files as published, so they differ from the digests of the
originals; [history-map.txt](../history-map.txt) says the same.

### 000-029

The 60 files 000-029 were committed as byte-for-byte copies of the verifier's inbox as of
2026-11-03 20:30 UTC (the concatenation's sha256 was checked against the
source after copying). The time can't be checked from the repository; the
hash pins the content. The 60 files concatenated in name order
(`cat evidence/verifier/0[0-2]?-*.md | sha256sum`, in the C locale) had
sha256 `f8293aa094267dad4f46266223bc03f0350f269abb1966b655f68721a568c561`,
in the inbox and as first committed; 4 of them were redacted on 2026-11-15 and 2026-11-16 ([024 and 026](#024-and-026-on-2026-11-15-and-2026-11-16), with the sha256 since then). Before committing they were searched for the
following, to be redacted:

| Searched for | Found | Redacted |
|---|---|---|
| tailnet hostnames (`*.ts.net`) | none | nothing |
| tailnet addresses (100.64.0.0/10) | none | nothing |
| private LAN addresses (192.168.x.x) | none | nothing |
| email addresses | none (the only `@` are OCaml/dune syntax such as `@wave_test/runtest`, and Python decorators) | nothing |
| the build machine's provider hostname | none | nothing |
| anything that looks like a credential or key (passwords, tokens, API keys, private-key blocks) | none | nothing |

So no file 000-029 differed from the verifier's own as first committed; 56 of the 60 still do not, and the 4 files of 024 and 026 differ by the replacements listed [below](#024-and-026-on-2026-11-15-and-2026-11-16). Runner names (`runner1`)
and paths under `~/protoemu-flow` are kept, as are the home-directory paths
there (`~/…`, and `/home/pi` and `/home/someone` in examples); none of them is
the local user's home directory. A later search of 000-029 for the classes
added below (that directory, the Kria's tailnet label, MAC addresses, SSIDs)
found none.

### 030-038

The 18 files 030-038 were copied byte for byte from the verifier's inbox on
2026-11-04 at about 04:55 UTC, checked equal to the source with `cmp`, and
then searched for the following, to be redacted. The time can't be checked
from the repository; the sha256 columns below are what pin the content.

This table counts by kind, not by line: `035-hostname-fix.md:9` holds both a
home directory and the build machine's hostname, and is counted under each.

| Searched for | Found | Redacted |
|---|---|---|
| the Kria's tailnet name or its bare label, in any case, with or without `ubuntu@` | 7 lines of `035-hostname-fix`, which quote the `git grep` needle and an upper-case plant | all 7 |
| other tailnet hostnames (`*.ts.net`) and addresses (100.64.0.0/10) | none (both named only as classes to search for) | nothing |
| private LAN addresses (192.168.x.x) | none (named only as a class; `10.77.0.x` is the test's own network namespace) | nothing |
| the local user's home directory (`/home/` and the user name) | 4 lines in 035 and 036, where reports quote what was searched for | all 4 |
| the build machine's hostname | 2 lines in 035, and inside the two email addresses below | all |
| email addresses | 2 lines in 035 with the commit author's address. Kept: `modprobe@loop.service` (a systemd unit); the public organisational `forum@t13.org` and `asic-competition@janestreet.com`; and addresses on reserved or placeholder names that reach no one, `root@example.org`, `n@cocotb.test`, `ubuntu@kria.example`, `ubuntu@kria.local` | the 2 |
| MAC addresses other than the project's made-up locally administered ones | 1, the Kria's Ethernet MAC, quoted in [report 030](030-docs-fix.report.md). Kept: `02:00:de:ad:be:01` and the truncated `02:57:4c:…` (035), both locally administered | the 1 |
| Wi-Fi network names (SSIDs), and anything that looks like a credential or key (passwords, tokens, `psk=`, API keys, private-key blocks) | none (030 names "the WiFi network name" only as something to search for; "token" is always a USB token) | nothing |

Also kept, as they name no host, address or person: the bare provider names
`[build host provider redacted]` and `fastmail` in the sweep lists of 030 and 035, `josusanmartin`
(the public Tiny Tapeout module and GitHub name), the Kria's own hostname
`kria` and `/home/ubuntu/…` on it, and `~/…` paths.

Each redaction replaces exactly the redacted string with a bracketed note;
nothing else on the line changed:

| File | Line | What | Replaced with |
|---|---|---|---|
| `030-docs-fix.report.md` | 121 | the Kria's Ethernet MAC address | `[MAC redacted]` |
| `035-hostname-fix.md` | 4 | the tailnet label (the `git grep` needle) | `[tailnet label redacted]` |
| `035-hostname-fix.md` | 6 | the local user's home directory | `[home directory redacted]` |
| `035-hostname-fix.md` | 9 | the home directory, and the build machine's hostname | `[home directory redacted]`, `[build machine hostname redacted]` |
| `035-hostname-fix.report.md` | 8 | an email address (the commit author's, which holds the build machine's hostname) | `[email redacted]` |
| `035-hostname-fix.report.md` | 12 | the tailnet label | `[tailnet label redacted]` |
| `035-hostname-fix.report.md` | 14 | the home directory, twice | `[home directory redacted]` (twice) |
| `035-hostname-fix.report.md` | 23 | the tailnet label | `[tailnet label redacted]` |
| `035-hostname-fix.report.md` | 54 | the build machine's hostname | `[build machine hostname redacted]` |
| `035-hostname-fix.report.md` | 62 | the tailnet label | `[tailnet label redacted]` |
| `035-hostname-fix.report.md` | 63 | an email address, as line 8 | `[email redacted]` |
| `035-hostname-fix.report.md` | 67 | the tailnet name, in upper case | `[upper-case tailnet name redacted]` |
| `035-hostname-fix.report.md` | 68 | the tailnet label | `[tailnet label redacted]` |
| `035-hostname-fix.report.md` | 69 | the tailnet label | `[tailnet label redacted]` |
| `036-usbfs-zlp-fix.report.md` | 111 | the home directory | `[home directory redacted]` |

That is 17 replacements on 15 lines of 4 files (one row per line; lines 9 of
the 035 item and 14 of the 035 report carry two each); the other 14 files are
unchanged. The sha256 of each file, in the verifier's inbox and as committed
(`.gitattributes` keeps git from converting line endings under `evidence/`):

| File | sha256 in the inbox | sha256 committed |
|---|---|---|
| `030-docs-fix.md` | `0a8103e64534a4c5e6b089750e8e907b090c1b1965f4f04cd6f674220826009f` | `6e530981e7bc1ff03aafae548a6ccaf3a5f09044416b4d13d6480dbab20cc25a` |
| `030-docs-fix.report.md` | `6a8860e161cd43785c7851f2564f529a05f12c7d61a1f410c947c544562dacf0` | `280a694e2e22f9f68781945c78751636aed06ba29a6e84464338ce34cb1bba3d` |
| `031-fabric-ping-fix.md` | `ae4e9c4a046ff43d2e8329e1364a1237a98f3973c19d57669532879eff0c1298` | same |
| `031-fabric-ping-fix.report.md` | `c825d3510855865a1a65017c507945ac818b290d85a0732bf91ac45573721864` | same |
| `032-usbfs-zlp.md` | `c20809f77bfe77e583acaee51f9c6d91cb4d862595c3b85ddf049cca3065446e` | same |
| `032-usbfs-zlp.report.md` | `0ce7c70eb17d6dbc6ceef6ea2ba8fb15c2d1abcc9772e00c362d5698a24dcd8d` | same |
| `033-docs-fix-fix.md` | `322c1f53f3bc982454db142832450cd0e23500edf9fbc68abc6fa2b33eca26da` | same |
| `033-docs-fix-fix.report.md` | `cd6b6b09827657c9814d09ae0c4528cb1ebc30b48c9d21854172dc9f991d4ac4` | same |
| `034-fabric-ping-fix2.md` | `b598638c437596a794e5aa12659dfb804d95b5a99c645d7d70d2bee1a905e0e0` | same |
| `034-fabric-ping-fix2.report.md` | `e948c21a33f60a4084f9769a3a30c492b7963fa937e4c89ac73bf0a0439c8e10` | same |
| `035-hostname-fix.md` | `014f5abcba3e3ca269ad0f6b35698122c8f4bb61cd8b4af8eb6f13f0ba599b26` | `f348f9205cb81c83831ecd5bd462fca3c7d202dcf82bf5034d00ab2598c1a992` |
| `035-hostname-fix.report.md` | `c4cc402bbe16773a0dd025ed4dc1e5dfb354537d7dc1a9eaebfb4bcff6a3c426` | `604a8b77f54167144f1338956dede44493988a8eb7fc2e4ee9d01ed675391291` |
| `036-usbfs-zlp-fix.md` | `e8e3f538ffecca6bebe780206c0a6f99e1011d35763a943a117c6bf9ef45357e` | same |
| `036-usbfs-zlp-fix.report.md` | `e965765b018d5a294e9bec74c6b757d8ff752db3985b0f9f4ed413074c977629` | `97e498335c006346bd463ce5fb243efe0b6769a14352b04528bf36470bc22248` |
| `037-fabric-ping-fix3.md` | `6a3b019d2a3f369b27654b1a81aeb07998b88a14df2777a66017a1d4161559dd` | same |
| `037-fabric-ping-fix3.report.md` | `1fab16d697e00e338cd6e3c98ddc33ea6600089bbe98f23f04de70af5aec7d07` | same |
| `038-hostname-fix2.md` | `476dcdd65cce1f451bd2f2324563cdeda2d6a6c4bfab0a747cbc29ae05e57b16` | same |
| `038-hostname-fix2.report.md` | `d88c284ed74628e1f3669a93da16a4f542b7a43ad6e05a99bceb52e93cfa5113` | same |

To check: `sha256sum evidence/verifier/03[0-8]-*.md` gives the right-hand
column; where the inbox is at hand, the same over its files gives the
left-hand one, and `diff` of each pair shows only the lines above.

### 039-041

The 6 files 039-041 were copied byte for byte from the verifier's inbox on
2026-11-04 at about 06:37 UTC and checked equal to the source with `cmp`. As
above, the time can't be checked from the repository; the sha256 column below
is what pins the content. They were searched for every class in the 030-038
table, and also for the Kria's Ethernet MAC in any form (with colons, with
dashes, bare). Nothing needed redacting, so all 6 are unchanged:

* the only email addresses are kept ones listed above, quoted in
  [report 041](041-verifier-reports2.report.md)
  (`:41`) and item 041 (`:5`);
* the only MAC address is the locally administered `02:00:de:ad:be:01`, with
  the truncated `02:57:4c:…` ([report 041](041-verifier-reports2.report.md), `:42`);
* `ssid`, `psk`, `ts.net` and 100.64.0.0 appear only as classes searched
  for, and `/home/` only as `/home/ubuntu` on the board and the examples of
  000-029;
* `josusanmartin`, `[build host provider redacted]` and `fastmail` appear in item 041 (`:5`), as
  in the 030-038 list of what was kept.

[Item 041](041-verifier-reports2.md) is the item for this folder's update to 038, and
[report 041](041-verifier-reports2.report.md) is its
FAIL, kept as it stands: its one finding was "17 replacements on 16 lines",
now corrected above to 15 lines. Item 041's claim 1 repeats the wrong number,
and as a verbatim record it is left so.

| File | sha256 in the inbox | sha256 committed |
|---|---|---|
| `039-fabric-ping2.md` | `2d1a8e276c1cd0620ad91767e7390f061e8f5e2f5060bf615ba80cbc17881df8` | same |
| `039-fabric-ping2.report.md` | `80aa7f1b9fbd059a6c2cdf004a55656609953afdf167a31fc4aa0748d0682ba3` | same |
| `040-usbfs-zlp-fix2.md` | `8734c99b96c6e9e88a6f12cb3be90415e340e20ede9735351c1004b8ebce8542` | same |
| `040-usbfs-zlp-fix2.report.md` | `7d6a145913d749e4d667c40cd93b4a610e0f20143f9891b55e0b56bba8623f86` | same |
| `041-verifier-reports2.md` | `5a7b9b36cd97bf3475481d27b1dcc6d3238cfb154863a8bfe299235349336819` | `7517b182822f41914f69e2ef31e87339ed0859fc2eb9349f3160d8b2e99208e0` |
| `041-verifier-reports2.report.md` | `69982302656c9d42919681d5c427a816c75e6dc37e0d2d0d2935a1ab9fd892f8` | same |

To check: `sha256sum evidence/verifier/039-*.md evidence/verifier/04[01]-*.md`
gives the right-hand column.

### 042-050

The 18 files 042-050 were copied byte for byte from the verifier's inbox on
2026-11-04 at about 15:40 UTC and checked equal to the source with `cmp`. As
above, the time can't be checked from the repository; the sha256 columns
below are what pin the content. They were searched for every class in the
030-038 table, for the Kria's Ethernet MAC in any form (with colons, with
dashes, bare, and its NIC half), for any IPv4 or IPv6 address, and with a
case-insensitive `git grep` for the tailnet label, the build machine's
provider hostname and the tailnet domain. The 18 files 042-050, as
committed, pass that search. The tailnet label and the provider hostname are
not written in this README; the bare tailnet domain is, but only as a class
searched for, as in 030-041.

Two replacements were made, on 2 lines of 2 files. Neither line names a host:
both quote the bare tailnet domain as a class searched for. The same class
mention is kept, unredacted, in the files 030 (item and report), 035 (item
and report), 038 (report) and 041 (report), in three lines of this README
(`:170` and `:197`, the 000-029 and 030-038 tables, as `*.` and the domain;
`:275`, the 039-041 list, bare), and in one of `evidence/README.md` (`:28`,
its redaction summary); these line numbers are as of the 051-078b update,
which added rows above them. The two lines here are
redacted only because this update's rule is that the `git grep` above finds
nothing in the added files; each replaces exactly the redacted
string, inside its backticks, and nothing else on the line changed:

| File | Line | What | Replaced with |
|---|---|---|---|
| `042-verifier-reports2-fix.report.md` | 42 | the tailnet domain, quoted as a class searched for | `[tailnet domain redacted]` |
| `043-fabric-ping3.report.md` | 24 | the tailnet domain, quoted as a class searched for | `[tailnet domain redacted]` |

Found and kept, as they name no host, address or person:

* `100.64.0.0`, the tailnet range's base, quoted as a class searched for
  (report 042 `:42`), as in 039-041;
* the locally administered `02:00:de:ad:be:01` (report 042 `:41`);
* `/home/ubuntu`, `/home/pi` and `/home/someone` quoted from earlier files
  (item 042 `:6`, report 042 `:17`, `:43`, `:44`), and `/home/runner1/protoemu-flow/…` (report 043
  `:22`), the compute runner's own home, kept like `~/protoemu-flow`;
* `~/…` paths, and the words `[build host provider redacted]` and `fastmail` where report 042
  (`:36`) gives the line numbers of 041's kept list;
* `7.1.18.1` (report 047 `:35`), a USB 2.0 section number, and `10:18:26`
  (report 043 `:69`), a build time.

There are no email addresses (the `@` are `@pytest` decorators and
`@point`/`@deadline` annotations), no other `/home/` paths, no MAC beyond the
one above, no IP address other than that class, no SSID and nothing that
looks like a credential: `psk` and `ssid` appear only as classes searched for,
and "token" is a USB token.

None of the 18 files has a Markdown link, so none was turned into plain text:
the two `[…](…)` in report 042 (`:28`, `:76`) are inside code spans, quoting
link syntax.

| File | sha256 in the inbox | sha256 committed |
|---|---|---|
| `042-verifier-reports2-fix.md` | `d0590a46558ad85731065355fc916ecc6f9e5da2ed3aef4c04248366658ea394` | same |
| `042-verifier-reports2-fix.report.md` | `6d4e347f500e74c5047cdd40afd0a84423b8b79373c567119bb3f254782db76e` | `64aa416896a84fa559f83bc3e6e6ae899857e45eaf2c12a086b6c0ceb739e895` |
| `043-fabric-ping3.md` | `46df90460f89cf62fbb8e0c1b37fcd597cfa664a1d02037c31e550f3929123e0` | same |
| `043-fabric-ping3.report.md` | `a87661d767b734caf7871ee146aeb4bd99b4f9b1b721508df26f909cfeece6ee` | `9e40810a07e80a4f60dd2191de0939c541b00b58909217113dbe4055cba8503b` |
| `044-usb-guard.md` | `bacfa96a3d8738cccab5849ac07bd5b28b8c02415d0ca4af3b3ecde32e794aab` | same |
| `044-usb-guard.report.md` | `f6352412bd99d42440ed8bcc5ca31f212ad391375cd377947c610cac4834640a` | same |
| `045-usb-guard-fix.md` | `fa3cc636aeda2b9eacf0144a667c9641ec683bee745910464e529c4daef61830` | same |
| `045-usb-guard-fix.report.md` | `8c0e2411062458f6393f31e817f903bc4d840969448bfa3dd63d6f1feb9ad184` | same |
| `046-usb-guard-fix2.md` | `27393cd4c75d2207455c8959e13d1e64d8ce888e55db08941951091e8b7dc4d6` | same |
| `046-usb-guard-fix2.report.md` | `633d85ee0fd0899b3827885cec77b849d5e35bb0c63896e158495f66eaf9dc77` | same |
| `047-usb-ls-restage.md` | `2f6fde0393fa14f806a4a0f178a7178a2fbfbef76acee16ef4def334212ae158` | same |
| `047-usb-ls-restage.report.md` | `187b30e3305eb7d2cd410e48875bba5a36efeb79d4cc1adb564d28aede11aa3a` | same |
| `048-usb-guard2.md` | `82785eb1f340fb135dd5aaf46c9fc0b686bd169132533ac62b7895a89aa93a60` | same |
| `048-usb-guard2.report.md` | `56ee4d32aef12fb68cce7cdc8b6d92d608e1907fda6ff4e6a365de35a9d15e95` | same |
| `049-usb-guard2-fix.md` | `ff52859d2fba4239eda0e4dc1550cffe0c57650821297a582ad4a4a1135aa9e8` | same |
| `049-usb-guard2-fix.report.md` | `1231cb6b279a250921ee79e0f782819ee5d6d7edc9a659db186722c76965cdd8` | same |
| `050-usb-in-setup.md` | `65af519efe282cd82c8595cb1eae9a4baf68a53a65e03557087dd47307205128` | same |
| `050-usb-in-setup.report.md` | `abaad2bf5e660315431b42c35009c3eb87d3f20a15e0fd264d81ef21f2f0a58e` | same |

To check: `sha256sum evidence/verifier/04[2-9]-*.md evidence/verifier/050-*.md`
gives the right-hand column.

### 051-078b

The 64 files 051-078b (32 items and their 32 reports; item 069 had four
rounds, 069 to 069d, and item 078 two) were copied from the verifier's inbox
on 2026-11-11 at about 02:49 UTC, checked equal to the source with `cmp`, and
then redacted. As above, the time can't be checked from the repository; the
sha256 column below pins the content as committed.

[Report 065](065-public-history.report.md) asks (`:7`) not to be copied here
until its finding 1 is resolved. That finding, old commit hashes in the
published history that each depend on one private value, was fixed by the
second round of the rewrite ([report 066](066-public-history2.report.md),
PASS). The replacements below remove the same kind of token from the records
themselves (backlog item AL, from report 066's finding 1).

**What was searched for.** Every class of the 030-038 table; the Kria's
Ethernet MAC in any form; any IPv4 or IPv6 address; the tailnet label, its hex
part alone and the hex part split by one separator; the build machine's
hostname, its ident email and the provider word, in any case, and the
hostname's fixed part (its letters without the digits, which is also the
provider's naming scheme), bounded by non-letters, in any case; the local
user's home directory in every form `test/model/test_home_paths.py` knows (an
absolute or relative home path, the dashed directory-name form, `/Users/`,
`~` and the name) and Claude scratch paths; session ids; and, with AK's
`record_check.py` run against the old (private) object store, every
hexadecimal run that names an old object whose id depends on only one private
value. The table counts lines and replacements by kind; a line can hold more
than one.

| Searched for | Found | Redacted |
|---|---|---|
| the tailnet label's hex part | 2 lines, each the `git grep` needle an item quotes (060 item `:31`, 064 item `:22`) | both, as `[tailnet label's hex part redacted]` |
| tailnet names and addresses | 1 line that names the label-plus-domain form as a class to search for (068 item `:39`). Elsewhere the domain, `tail` and hex, the 100.64.0.0/10 range and the Tailscale IPv6 prefix appear only as classes, as in 042-050 | the 1, as `[tailnet name form redacted]`, so that `test_private_hosts.py` passes |
| the build machine's hostname or its ident email | none | nothing |
| the build machine's hostname class: its fixed part, the host name with its digits removed, which is also the provider's naming scheme (found by verifier 079, finding 1) | 5 lines, each in a list of what was searched for: 058 report `:30`, 065 item `:31` and 066 item `:141` (both beside the provider word), 067 item `:156`, 068 item `:71` | all 5, as `[build host name class redacted]` |
| the provider word | 3 lines, each in a list of what was searched for or kept (053 report `:61`, 065 item `:31`, 066 item `:141`) | all 3, as `[build host provider redacted]` |
| the local user's home directory | 4 lines: 051 item `:10`; 065 item `:32` (twice: the path, and `~` with the name) and `:67`; 066 item `:142` | all 5, as `[home directory redacted]` |
| scratch paths | 3 lines: the rewrite's working directory (065 item `:7`, 066 item `:11`), which also holds the home directory's dashed form and a session id; and a bare scratch prefix with a uid (051 item `:29`) | all 3, the path up to `scratchpad` (or the prefix) as `[scratch path redacted]` |
| other people's and generic home directories that `test_home_paths.py` doesn't allow under `evidence/verifier/` (verifier note 078b-4) | 13 lines: the upstream template author's home that `test/tb.gtkw` once held, the devcontainer's user, a test's made-up `HOME`, the CI runner's user, and a macOS `/Users/` example | all 15, as `/home/<other>` or `/Users/<other>`; the names the test allows here (`ubuntu`, `runner1`, `pi`, `someone`) are kept |
| old commit ids that depend on one private value (the checker's "commit host-only") | 6, on 6 lines | 4 translated to the published commit, 2 marked (below) |
| old tree ids that depend on one private value ("tree host-only") | 3: 063 report `:39`, 064 report `:75`, 065 report `:86` | all 3, as `[host-only tree id redacted]` |
| ids of the rewrite's unpublished first round (item 065's output) | 20, on 12 lines of item and report 065 | all 20, as `[round-1 id redacted]` |
| email addresses | only `root@example.org` (058 report `:31`) and the GitHub noreply address in the rewritten ident (065 item `:36`) | nothing |
| MAC addresses | only the locally administered `02:00:de:ad:be:01` and `02:57:4c:…`, and the broadcast `ff:ff:…` | nothing |
| IP addresses | none beyond the range base 100.64.0.0, quoted as a class: `0.0.0.0` and `3.4.1.1` (053 report `:49`), `7.1.18.1` (a USB 2.0 section number) and `10.77.0.2` (the test's own network namespace); the other matches are times such as `0:07:22` | nothing |
| Wi-Fi names (SSIDs), credentials and keys | none: `ssid`, `psk`, `password` and key headers appear only as classes searched for; "token" is a USB token, and "secrets" names the rewrite's private values, never a value | nothing |

That is 63 replacements on 47 lines of 17 files; the other 47 files are
unchanged. (Of the 5 lines with the hostname class, 065 item `:31` and 066
item `:141` also hold a provider-word replacement, and only 067 item is a
file with no other replacement.) Each replacement changes exactly the redacted string (inside its
backticks where it had them) and nothing else on the line. No file has a
Markdown link outside a code span, so none was turned into plain text.

Why each new marker:

* `[tailnet label's hex part redacted]`: the hex part is the label's only
  private part; the existing `[tailnet label redacted]` is for the whole label.
* `[tailnet name form redacted]`: a class mention, not a name, replaced only so
  that the tree-wide tailnet scan finds nothing.
* `[build host name class redacted]`: the fixed part of the build machine's
  name, which leaves only its digits to guess and names the provider (it is
  the provider's naming scheme for machines).
* `[scratch path redacted]`: a Claude scratch directory, which names the local
  user and a session.
* `/home/<other>`, `/Users/<other>`: a home directory that is not the local
  user's, named in a report about the home-path scan; redacted so that the
  scan passes without widening its allowlist.
* `[host-only tree id redacted]`: an old tree whose id depends on the build
  host's name alone (through old commit ids that its files cite). Trees have no
  published counterpart, so the id can't be translated, and a guess at the name
  could be tested against it.
* `[old id of a907d4a, redacted]`: in the old-to-new tables of items 065 and 066
  (row `signoff-g4`), the old commit is host-only. Writing its published id in
  its place would make the row read `a907d4a` to `a907d4a` (066), or pair it
  with a round-1 id it never had (065), so the cell names the published commit
  instead.
* `[round-1 id redacted]`: round 1 of the rewrite (065) was never published. Its
  files still cited old host-only commit ids (the pass that translated them came
  in round 2), so its commit and tag ids depend on the build host's name. The
  checker can't classify them, as they are not in the old object store, but
  verifier 079 (check 4) computed them over the round-1 store: counting every
  hex token already in public text as known, 12 of the 13 redacted round-1
  objects need only the build host to be tested (main^ and the tree at 065
  report `:86` also need the guessable provider word), and only round-1 main
  needs the label too. Report 066 gives the published ids. The verifier counts
  21 tokens in 13 objects; the table above counts 20 in 12, because the tree
  at 065 report `:86` is also an old host-only tree (round 1 kept b2fa7f3's
  tree) and is counted on that row.

The 4 translated ids: 060 report `:111` is now `c8f311e`, the root commit of
public main, in which `test/tb.gtkw` arrived; 061 report `:26` is `37dd63a`,
and `:27` and `:53` are `878a876`. Each is the published counterpart of the
old commit, from the rewrite's private commit map, an ancestor of public main,
and a unique prefix there.

**The hexadecimal strings that remain.** Every run of 7 or more hexadecimal
characters in the 64 files, as committed, was looked up in the old object
store and in this branch's and public main's objects:

* 449 occurrences (64 distinct) are commits in this branch's or public main's
  history (the 4 translations and 2 markers above included); 5 more are trees,
  3 tag objects and 2 blobs of public main, and 1 is the published
  `signoff-g4` tag object (066 item `:28`).
* 651 occurrences are old commits, counted as verifier 079 counts them:
  lowercase tokens of 7 to 40 hex, bounded by non-alphanumerics, that prefix
  an old commit of the old object store. 624 are in 051-068 (the items and
  reports written before or about the rewrite): 515 (57 distinct) have a
  published counterpart, and 109 (16 distinct) have none (builder commits
  that were rebased or never merged). The other 27 (3 distinct, all with a
  counterpart) are in 069-078b, 25 of them `b2fa7f3` in the ancestry checks. 2 more are old trees: 053 report `:79` and 066
  report `:46`, the second also cited in `docs/`. AK's checker classes every
  one of these as joint-only: its id depends on two private values, the build
  host's name and the tailnet label, so it can't be used to test a guess at
  either alone. RULES allow joint-only ids in published files, so they are
  kept verbatim. They are not in this repository under these ids.
  `evidence/history-map.txt`, written at the rewrite, already lists all 16
  without a counterpart (as cited but not published) and 20 of the 58 with
  one; the other 38 are not in it.
* 29 occurrences (7 distinct) are commits made after the rewrite, from public
  main, on branches not (yet) in this branch's history (for example 073's
  `72e2ccb`); none has a private input.
* The rest are not objects: dates, sha256, CRC and ID values, process ids,
  the verifier's checkout suffixes, and hex words such as `deadbeef`.

As committed, `record_check.py --repo . --tree HEAD --commits
refs/remotes/public/main..HEAD` flags nothing.

The sha256 of each file as committed. For a file with replacements, the
digest of the inbox original is not given: the original holds the redacted
values, and its digest would let a guess at them be checked (report 066
finding 4 found this for the provider word in the 030-042 digests above). An
unchanged file has the same digest in the inbox.

| File | sha256 committed | Same bytes as the inbox |
|---|---|---|
| `051-verifier-reports3.md` | `629d17053e1276b3c1e8a3c0b5e1ce5d36b7523927966488fd940db41ffbb2ae` | no: lines 10, 29 |
| `051-verifier-reports3.report.md` | `05548b29801501aab882c92c75d32a5ac5adbe1eba3b18ef0837d65fb39cfb7a` | yes |
| `052-demo-notes.md` | `3497e0e1a7ccce2840f4b4d705475db180492952589df94733e5ac0d79e26a7e` | yes |
| `052-demo-notes.report.md` | `9dc1eb14981bac7b9c7a4967fdcd166774e3aee5330b2e18580e32bf2c829159` | yes |
| `053-verifier-reports3-fix.md` | `7691897b0d439b41488f82bd215eba01c07a05e547b5db7e9a8eda92489107bc` | yes |
| `053-verifier-reports3-fix.report.md` | `b1bd64155d044e127eec1058edbed7c0f584447f72d3703209486cb8bb12b438` | no: line 61 |
| `054-reattach-wiring.md` | `49a2c0f0e6c9215d02ced3bedc8b111fceacd62256852d7ef173e17b52116ed4` | yes |
| `054-reattach-wiring.report.md` | `775714d417e5d2e34815471ac31792022afb15d6531db880208d617c6b56f299` | yes |
| `055-reap-stop.md` | `631bbcb83a1a3a6b3c3a394eaf055c5490c0ba41810db2186ee01a30872c3f82` | yes |
| `055-reap-stop.report.md` | `456cc312a4c51e8d99215030d554671dd5343c95214ca96c9f8eb36100136b3b` | yes |
| `056-ocaml-parity2.md` | `a332cca60ffef14158bdff3d6cacd3255d06283fbee7c091d664c1befe866400` | yes |
| `056-ocaml-parity2.report.md` | `01710047e42774ba24593728f5d1833d7eef1b688b7a0382fb108fd4c56250af` | yes |
| `057-present.md` | `37fc7e8cb446f08ddd4b469d4e18a2c4fa7a36b7c84b043e120560c5a34ba62a` | yes |
| `057-present.report.md` | `db7927babec2d325df27b077855c27df96cfe47b714d53a6a82810518ea368e7` | yes |
| `058-tidy.md` | `cd716d6c154d97713265ed683823e0cff7795dae8cfde5c2a4531bbe493bb786` | yes |
| `058-tidy.report.md` | `a24460cd63ffd98436e869e83af3699273eada8e77963848df4d5e98984eca63` | no: lines 27, 28, 29, 30 |
| `059-present-fix.md` | `6453b4602b64a92a928f6fab40c16f91c02b494a5ce4766ae0fe44d8c1ebbe14` | yes |
| `059-present-fix.report.md` | `749900870e72a3034ad9f2e96dbd3767d9960e27d169d0bf5c2f3ea5e1181e56` | yes |
| `060-privacy-needles.md` | `e8a231627993b05699943455cfec814e20fe4bb331f2a14d8607966459504927` | no: lines 31, 47 |
| `060-privacy-needles.report.md` | `c11bca27ccfa3b75ee6fb69f694592da00231ddb4c9b1e26c5223b7ec493df0b` | no: lines 109, 111 |
| `061-present2.md` | `0bea292215e679f5710e73f7f55d68db800e367a62f83c0041f9e382537058eb` | yes |
| `061-present2.report.md` | `10114d9826560c02cda3db49924b8639697262d36369df6c39e94f6902355492` | no: lines 26, 27, 53 |
| `062-usb-config-prestage.md` | `99409d24a4ac9278338bb8b13b0d661abc69384ad9df90a46fba17bdafd44fd4` | yes |
| `062-usb-config-prestage.report.md` | `2d37dd465f0679893d25853f97fb15e8c75720adf7d2b4213f73c9bcaa58b3fd` | yes |
| `063-present3.md` | `ff3205e66623834d27c918714d41fafa0134d0ec7c7ae8974e9deb85609f1e4a` | yes |
| `063-present3.report.md` | `dafd1d3b6c188d583630a09a79f24832b7fc54ff9f2c6729dc202d19381e0c7b` | no: line 39 |
| `064-privacy-needles2.md` | `1b403d22c46786f23f72135c125cf1c69d7def29123294d69a26ec83648f2282` | no: line 22 |
| `064-privacy-needles2.report.md` | `440aa7edd39c169f7fc0b4ffb9df86d4753a4a7a566124da2c5db5a3b4b00538` | no: line 75 |
| `065-public-history.md` | `355e3361c94a6eb38ba23775dcf9628630bf249f4b7358394e34441abbceea18` | no: lines 7, 18, 19, 20, 21, 22, 23, 31, 32, 62, 67 |
| `065-public-history.report.md` | `a370fe8e9d819c5ba51df873ab8af2de745a6d88c75e596c839ce6ff4efd1ad1` | no: lines 86, 106, 114, 128, 154 |
| `066-public-history2.md` | `968e96fa08a799f7e3758a9cae9e56480c9eb50dfc75821c697d79d88687c65d` | no: lines 11, 28, 141, 142 |
| `066-public-history2.report.md` | `b8072a6eb3f85b886af53c0db7fe493ad9ca452aae9d5aaf4c0cd9a7808b9348` | yes |
| `067-public-followups.md` | `c3c3cdc1d574a926989aa9c5bdf238732eb1fcae24444479da43777497f42189` | no: line 156 |
| `067-public-followups.report.md` | `32da09f9ba2975cbc22adfb1fe257b9521fb9a748c498674c2e50d9d6178e870` | yes |
| `068-public-followups2.md` | `07d55fbb3d423e777e2e0495c18c75d2dfb2750c7a6ee6ccc4df92f15729ee67` | no: lines 39, 71 |
| `068-public-followups2.report.md` | `6b5110b2cbd4f4878fd70d206b0bac7557b4240c1a6efd99cf7d67c9481dc05a` | yes |
| `069-8x4-study.md` | `eb886c3e350e2380e86d7a1f8e29bb7952e772c656d61324dad18d6a7500f65a` | yes |
| `069-8x4-study.report.md` | `66908b851e75d3a326a07df764f3a168827bc773150a4bae90663b1da92567cd` | yes |
| `069b-8x4-study-fix.md` | `fb2129e2dfadf2a91557e8cad8fc0ba35766af961620ba5f490543a804812ca4` | yes |
| `069b-8x4-study-fix.report.md` | `667fd1610dd59df8981ce7b6c75d4384bcf88e2d5e4ca7a1b6d7548a2a69c86a` | yes |
| `069c-8x4-study-fix2.md` | `574130316934ad47c4372fa6b28a4c63a39163a3b4fa12450c668160ab7a629f` | yes |
| `069c-8x4-study-fix2.report.md` | `ee4f08b5aaf9bf981c677c41b52d5a2b7e7e97f7226f0439ad301986686c22b6` | yes |
| `069d-8x4-study-fix3.md` | `17ba28fc561ca2f7363888765ccd330d6dff3e65a6d61662f029b6281a982911` | yes |
| `069d-8x4-study-fix3.report.md` | `c345c4c58429c7bf361f1eea9bea416ab2250901b903207cc73a4f2b9ad743ac` | yes |
| `070-pkg3-spec-model.md` | `6cadc8eefebab09846ae8aa11a6cf8f54c1f76d302c83cfb8b057270c2b68a71` | yes |
| `070-pkg3-spec-model.report.md` | `87debbf3b52bcb35f09453a7b6625cc03d007652a2d27b79dc0c2c789e02bc8b` | yes |
| `071-pkg3-rtl.md` | `b2cef9340d51dd78b3537820bd0c771114f8e8c4ea80ae7ec2bc37f54856426a` | yes |
| `071-pkg3-rtl.report.md` | `afce321c0bcad5595dd29098d23eeca34d5206edff6fc864f9b6bea58830d62f` | yes |
| `072-pkg3-firmware.md` | `cb4a5b83c4c51a0a99e77c0f9e342990cbc4e84c3b1b22bd164969ef6bbf9007` | yes |
| `072-pkg3-firmware.report.md` | `c41dc327db9ad081e4d2ef73a977bcd1e84805068325130d1ec239ba66c5897a` | yes |
| `073-flow-8x4.md` | `8e5bf0a6790dddb60bfd2bca882cd8fbb5fb4154442a82aa5c3843579333bbc5` | yes |
| `073-flow-8x4.report.md` | `287c80ddd297a5b6b8bda7aa1f8ea9e708eab8de1987487d7cd994c34d55cbfe` | yes |
| `074-pkg3-fix.md` | `5d1dcad94b2a90a9ade111d6a4bf40a1d22340600d95e7f73c1ec4bbd886c151` | yes |
| `074-pkg3-fix.report.md` | `18b8d8ab7c01ce6f95416911e3a709d2c2fde0bc698ac62b0f174a7ef5c88b77` | yes |
| `075-merge-ao-ap.md` | `abed08c8685272232f8918447bceb214b47e9f6819099852385a8a3d4509b359` | yes |
| `075-merge-ao-ap.report.md` | `9dc28c2fdaa1e110f91e56a18e0ba097fe516d52c593c5701af18ebbfb314f4e` | yes |
| `076-kv260-3core.md` | `2f3b989d7dab36974b7137f6245981da6784603046d16e057af95b65c01f5e33` | yes |
| `076-kv260-3core.report.md` | `c5a10dbe0047088a8286c60e12dad63088d52b05483cff27f25d8acd0ee31361` | yes |
| `077-kv260-3core-fix.md` | `831eaa004bb6a62fc9996f078d20d5c4b9481ab8bab55c9da1072b37f6c06f46` | yes |
| `077-kv260-3core-fix.report.md` | `29293ff63759ef0755b7196e6f2cfb87232d3eb15a253a07958b2c1136311546` | no: line 94 |
| `078-home-path-scan.md` | `0720ab5b68676842363f2019f5ed5fac4a56ad53a9a44c9044a6b7eb29a06e0c` | no: lines 47, 58, 61, 67, 76 |
| `078-home-path-scan.report.md` | `69df179d179b968dbc3dd504a9344a7c7e5029fe4165ed08fb9b0d47712ce512` | yes |
| `078b-home-path-fix.md` | `92e59e6968cc84974ea17293582ba298f584d78e0709b33c701e7932c159fa24` | no: line 79 |
| `078b-home-path-fix.report.md` | `c3faf5aa28e56f45917ab6ac56979c0d70509dcfc62eea8d8eb90b82bdec24d9` | yes |

To check: `sha256sum evidence/verifier/05[1-9]-*.md evidence/verifier/06*-*.md evidence/verifier/07[0-8]*-*.md`
gives the middle column.

### 024 and 026, on 2026-11-15 and 2026-11-16

Items 024 and 026 and their reports check a survey of other entries to the
competition that `docs/rules-check.md` held at the time. The survey of other
entries is not part of this repository's published record (the section is no
longer in that document), so on 2026-11-15 the strings that identify those
entries were replaced in these 4 files as well. On 2026-11-16 three more
strings were replaced in the two files of 024: two descriptions and a file
name, none of them a name, that a reader who knows the field could match to
two entries (a verifier finding on the first round; its report is not in this
repository). The same content remains in the repository's earlier history.
This is not a privacy redaction.

**What was searched for.** The 4 files were read in full, and every tracked
file was searched, in any case, for each entry, author and repository name of
the removed survey. As committed, those names are in no tracked file, apart
from the credit to PRISM (below). Replaced, in the 4 files:

* the name of another entry or of its author, as `[other entry redacted]`;
* its repository (`owner/name`), as `[other entry's repository redacted]`, a
  branch of it, as `[other entry's branch redacted]`, and the name of a
  release, as `[other entry's release redacted]`;
* a file path of its repository, the file name of a release, or a file name
  inside a release's archive that only that entry has, as
  `[other entry's file redacted]`;
* text quoted from its repository or release that is its own wording, as
  `[other entry's text redacted]`;
* the item's description of an entry (what it had shown, and how), where the
  description could identify the entry, as
  `[other entry's description redacted]`.

Kept: the verifier's verdicts, findings and reasoning; what it says of an
entry in its own words, with dates and numbers (apart from the two
descriptions above); quotes of this repository's
own documents; file names that every Tiny Tapeout project has (`info.yaml`,
`gds.yaml`) and generic bare file names inside a downloaded archive (`run.txt`,
`pico_b.log`, `ledger.txt`); Linux kernel log text; names of
boards and instruments; and the names of Tiny Tapeout's repositories and of
the competition's organisers. Also kept: the credit to PRISM
(`kdp1965/ihp-um-janestreet-prism`), from which this repository copies files
under Apache-2.0 (item 024 `:8`; report 024 `:7`, `:114` and `:123`).

Each replacement changes exactly the redacted string (inside its backticks,
quotation marks or parentheses where it had them; a line number after a file
path is kept) and nothing else on the line. One row per line:

| File | Line | What | Replaced with |
|---|---|---|---|
| `024-rules-check.md` | 8 | the name of another entry or of its author, twice; a description of it, twice | `[other entry redacted]` (twice), `[other entry's description redacted]` (twice) |
| `024-rules-check.report.md` | 45 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 47 | the name of another entry or of its author, twice | `[other entry redacted]` (twice) |
| `024-rules-check.report.md` | 54 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 55 | its release's name; a file path of its repository or release | `[other entry's release redacted]`, `[other entry's file redacted]` |
| `024-rules-check.report.md` | 56 | a file path of its repository or release; text quoted from it | `[other entry's file redacted]`, `[other entry's text redacted]` |
| `024-rules-check.report.md` | 57 | a file path of its repository or release, twice | `[other entry's file redacted]` (twice) |
| `024-rules-check.report.md` | 58 | the name of another entry or of its author; a file path of its repository or release, twice; text quoted from it | `[other entry redacted]`, `[other entry's file redacted]` (twice), `[other entry's text redacted]` |
| `024-rules-check.report.md` | 100 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 101 | the name of another entry or of its author, three times | `[other entry redacted]` (three times) |
| `024-rules-check.report.md` | 102 | the name of another entry or of its author; its repository; a branch of its repository | `[other entry redacted]`, `[other entry's repository redacted]`, `[other entry's branch redacted]` |
| `024-rules-check.report.md` | 106 | the name of another entry or of its author; text quoted from it | `[other entry redacted]`, `[other entry's text redacted]` |
| `024-rules-check.report.md` | 107 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 108 | the name of another entry or of its author; text quoted from it; a file path of its repository or release | `[other entry redacted]`, `[other entry's text redacted]`, `[other entry's file redacted]` |
| `024-rules-check.report.md` | 109 | the name of another entry or of its author; text quoted from it | `[other entry redacted]`, `[other entry's text redacted]` |
| `024-rules-check.report.md` | 110 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 111 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 112 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 113 | the name of another entry or of its author | `[other entry redacted]` |
| `024-rules-check.report.md` | 139 | the name of another entry or of its author | `[other entry redacted]` |
| `026-rules-check-fix.md` | 7 | the name of another entry or of its author | `[other entry redacted]` |
| `026-rules-check-fix.report.md` | 10 | the name of another entry or of its author | `[other entry redacted]` |
| `026-rules-check-fix.report.md` | 53 | the name of another entry or of its author | `[other entry redacted]` |
| `026-rules-check-fix.report.md` | 54 | a file path of its repository or release, twice | `[other entry's file redacted]` (twice) |
| `026-rules-check-fix.report.md` | 55 | a file path of its repository or release | `[other entry's file redacted]` |
| `026-rules-check-fix.report.md` | 58 | a file path of its repository or release; text quoted from it | `[other entry's file redacted]`, `[other entry's text redacted]` |
| `026-rules-check-fix.report.md` | 59 | a file path of its repository or release | `[other entry's file redacted]` |
| `026-rules-check-fix.report.md` | 75 | the name of another entry or of its author | `[other entry redacted]` |
| `026-rules-check-fix.report.md` | 80 | a file path of its repository or release | `[other entry's file redacted]` |
| `026-rules-check-fix.report.md` | 95 | the name of another entry or of its author; a file path of its repository or release, twice | `[other entry redacted]`, `[other entry's file redacted]` (twice) |
| `026-rules-check-fix.report.md` | 96 | its release's name; its repository | `[other entry's release redacted]`, `[other entry's repository redacted]` |

That is 54 replacements on 31 lines of 4 files (26 names, 2 repositories, 1
branch, 2 release names, 15 file paths, 6 quoted texts and 2 descriptions);
the other 56 files 000-029 are unchanged. Of the 54, 51 were made on
2026-11-15; the two descriptions (item 024, line 8) and one of the file names
(report 024, line 56) were made on 2026-11-16, on lines that already held a
replacement, so the count of lines did not change. The sha256 of each of the
4 files as committed:

| File | sha256 committed |
|---|---|
| `024-rules-check.md` | `a7c4cf346a77c1a52387ad202caa53786706f258cb11fd683f263bd28cd099df` |
| `024-rules-check.report.md` | `55298fce32210f64fd4165701c065937bbd46bb459eaf6c047bf01708eae302b` |
| `026-rules-check-fix.md` | `d2b42bce56ca50939465fedf446d39a62f203d74bfbefae3881679bb03e0e6ac` |
| `026-rules-check-fix.report.md` | `b423f30533eb49e0bc152013e38376372837f1de848b1975470c22d41c87905d` |

To check: `sha256sum evidence/verifier/02[46]-*.md` gives the right-hand
column, and the 60 files 000-029 concatenated as [above](#000-029) now have
sha256 `5247186da6ff418778269056d75de069f517c8f9ddc98196d6b597ec088d5a72`.
Between the two dates the two files of 024 had sha256 `44ec3d3b…` and
`56826697…`, and the concatenation `03ef7d7b…`.

Item 079 and later items and reports are not here.
