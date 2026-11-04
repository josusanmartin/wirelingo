# Verifier reports

Every report of the project's independent verifier, with the item it
answered, from the first (2026-11-02) to item 050 (2026-11-04): 51 items, 51
reports, 27 `VERDICT: PASS` and 24 `VERDICT: FAIL`. FAILs are kept: they are
most of what the verifier caught. (Counted from the files, not from this
text: 51 `NNN-name.md` files that are not `.report.md`, 51 `NNN-name.report.md`
files, and `head -qn1 *.report.md | sort | uniq -c`, which gives 27 `VERDICT:
PASS` and 24 `VERDICT: FAIL` and no other first line.)

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

Commits named here are local commits of the project. Every hexadecimal string
of 7 or more characters in the items and reports 000-050 was looked up with
`git cat-file --batch-check` and, where it is a commit, against `git rev-list
HEAD`. All the commits are in this branch's history except these, which name
real commits of the repository that this branch does not contain:

| Commit | Named in | What it is |
|---|---|---|
| `a85fc57` | item and report 041; item 042, report 042 | this folder's update to 038 before it was rebased onto `bc98a97`; the same change, with report 041's finding fixed, is in this branch's history as `763e0af` and `ead2d02` |
| `e158fe0` | item and report 048; item 049, report 049 | the `usb-guard2` commit before it was rebased onto `9baebb7`; `git range-diff` gives it equal to `ac3e72e`, which is in this branch's history (report 049) |
| `dc51cc8` | report 047 | the `usb-ls-restage` test commit before the rebase onto `edc9042`; the evidence README of `model-usb-ls-restage-20261104/` maps it to `c887e28`, in this branch's history |
| `8fdda85` | report 047 | the `usb_ls_tx` fix before the same rebase; mapped to `699e430`, in this branch's history |
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
| 000 | `eth10-match` | `61af9b2` | FAIL | [item](000-eth10-match.md) | [report](000-eth10-match.report.md) |
| 001 | `eth10-match-fix` | `aedf991` | FAIL | [item](001-eth10-match-fix.md) | [report](001-eth10-match-fix.report.md) |
| 002 | `rmii-study` | `d811228` | FAIL | [item](002-rmii-study.md) | [report](002-rmii-study.report.md) |
| 003 | `eth-stack` | `e71ab1c` | FAIL | [item](003-eth-stack.md) | [report](003-eth-stack.report.md) |
| 004 | `eth10-match-fix2` | `1fddeef` | FAIL | [item](004-eth10-match-fix2.md) | [report](004-eth10-match-fix2.report.md) |
| 005 | `explain-docs` | `7eaaf9c` | PASS | [item](005-explain-docs.md) | [report](005-explain-docs.report.md) |
| 006 | `ocaml-port` | `03edc75` | FAIL | [item](006-ocaml-port.md) | [report](006-ocaml-port.report.md) |
| 007 | `rmii-fix` | `ceb84a4` | PASS | [item](007-rmii-fix.md) | [report](007-rmii-fix.report.md) |
| 008 | `bench-fw` | `5582d7a` | PASS | [item](008-bench-fw.md) | [report](008-bench-fw.report.md) |
| 009 | `eth10-match-fix3` | `0bdcbe5` | PASS | [item](009-eth10-match-fix3.md) | [report](009-eth10-match-fix3.report.md) |
| 010 | `eth-stack-fix` | `d2d89b8` | FAIL | [item](010-eth-stack-fix.md) | [report](010-eth-stack-fix.report.md) |
| 011 | `bench-fw-notes` | `f47e875` | PASS | [item](011-bench-fw-notes.md) | [report](011-bench-fw-notes.report.md) |
| 012 | `blind-test` | `40fb59a` | FAIL | [item](012-blind-test.md) | [report](012-blind-test.report.md) |
| 013 | `ocaml-fix` | `cca723c` | PASS | [item](013-ocaml-fix.md) | [report](013-ocaml-fix.report.md) |
| 014 | `faults-pulse` | `75eed67` | PASS | [item](014-faults-pulse.md) | [report](014-faults-pulse.report.md) |
| 015 | `eth-stack-fix2` | `87a1b70` | PASS | [item](015-eth-stack-fix2.md) | [report](015-eth-stack-fix2.report.md) |
| 016 | `hw-kit` | `49a2d89` | FAIL | [item](016-hw-kit.md) | [report](016-hw-kit.report.md) |
| 017 | `ocaml-notes` | `7dba86e` | FAIL | [item](017-ocaml-notes.md) | [report](017-ocaml-notes.report.md) |
| 018 | `blind-fix` | `e5dd3d0` | PASS | [item](018-blind-fix.md) | [report](018-blind-fix.report.md) |
| 019 | `fabric-guard` | `80cc0b9` | FAIL | [item](019-fabric-guard.md) | [report](019-fabric-guard.report.md) |
| 020 | `hw-kit-fix` | `8a6dd8f` | PASS | [item](020-hw-kit-fix.md) | [report](020-hw-kit-fix.report.md) |
| 021 | `ocaml-selfcheck` | `88ab957` | PASS | [item](021-ocaml-selfcheck.md) | [report](021-ocaml-selfcheck.report.md) |
| 022 | `guard-fix` | `851a373` | PASS | [item](022-guard-fix.md) | [report](022-guard-fix.report.md) |
| 023 | `fault-fixes` | `b5496aa` | FAIL | [item](023-fault-fixes.md) | [report](023-fault-fixes.report.md) |
| 024 | `rules-check` | `7e19431` | FAIL | [item](024-rules-check.md) | [report](024-rules-check.report.md) |
| 025 | `reaction` | `f497922` | PASS | [item](025-reaction.md) | [report](025-reaction.report.md) |
| 026 | `rules-check-fix` | `f3829df` | PASS | [item](026-rules-check-fix.md) | [report](026-rules-check-fix.report.md) |
| 027 | `fault-fixes-fix` | `7f7a70f` | FAIL | [item](027-fault-fixes-fix.md) | [report](027-fault-fixes-fix.report.md) |
| 028 | `fault-fixes-labels` | `b2560de` | PASS | [item](028-fault-fixes-labels.md) | [report](028-fault-fixes-labels.report.md) |
| 029 | `fabric-ping` | `da82a51` | FAIL | [item](029-fabric-ping.md) | [report](029-fabric-ping.report.md) |
| 030 | `docs-fix` | `3410bc9` | FAIL | [item](030-docs-fix.md) | [report](030-docs-fix.report.md) |
| 031 | `fabric-ping-fix` | `48fcce0` | FAIL | [item](031-fabric-ping-fix.md) | [report](031-fabric-ping-fix.report.md) |
| 032 | `usbfs-zlp` | `6dc8c93` | FAIL | [item](032-usbfs-zlp.md) | [report](032-usbfs-zlp.report.md) |
| 033 | `docs-fix-fix` | `f3b066b` | PASS | [item](033-docs-fix-fix.md) | [report](033-docs-fix-fix.report.md) |
| 034 | `fabric-ping-fix2` | `c4ea377` | FAIL | [item](034-fabric-ping-fix2.md) | [report](034-fabric-ping-fix2.report.md) |
| 035 | `hostname-fix` | `2650f98` | PASS | [item](035-hostname-fix.md) | [report](035-hostname-fix.report.md) |
| 036 | `usbfs-zlp-fix` | `f88fb31` | FAIL | [item](036-usbfs-zlp-fix.md) | [report](036-usbfs-zlp-fix.report.md) |
| 037 | `fabric-ping-fix3` | `1b3ff6b` | PASS | [item](037-fabric-ping-fix3.md) | [report](037-fabric-ping-fix3.report.md) |
| 038 | `hostname-fix2` | `bc280ff` | PASS | [item](038-hostname-fix2.md) | [report](038-hostname-fix2.report.md) |
| 039 | `fabric-ping2` | `651513e` | PASS | [item](039-fabric-ping2.md) | [report](039-fabric-ping2.report.md) |
| 040 | `usbfs-zlp-fix2` | `05f86ec` | PASS | [item](040-usbfs-zlp-fix2.md) | [report](040-usbfs-zlp-fix2.report.md) |
| 041 | `verifier-reports2` | `a85fc57` | FAIL | [item](041-verifier-reports2.md) | [report](041-verifier-reports2.report.md) |
| 042 | `verifier-reports2-fix` | `7d53740` | PASS | [item](042-verifier-reports2-fix.md) | [report](042-verifier-reports2-fix.report.md) |
| 043 | `fabric-ping3` | `5d5eb04` | PASS | [item](043-fabric-ping3.md) | [report](043-fabric-ping3.report.md) |
| 044 | `usb-guard` | `5554b88` | FAIL | [item](044-usb-guard.md) | [report](044-usb-guard.report.md) |
| 045 | `usb-guard-fix` | `326de49` | FAIL | [item](045-usb-guard-fix.md) | [report](045-usb-guard-fix.report.md) |
| 046 | `usb-guard-fix2` | `edc9042` | PASS | [item](046-usb-guard-fix2.md) | [report](046-usb-guard-fix2.report.md) |
| 047 | `usb-ls-restage` | `6e1da04` | PASS | [item](047-usb-ls-restage.md) | [report](047-usb-ls-restage.report.md) |
| 048 | `usb-guard2` | `e158fe0` | FAIL | [item](048-usb-guard2.md) | [report](048-usb-guard2.report.md) |
| 049 | `usb-guard2-fix` | `0135ca2` | PASS | [item](049-usb-guard2-fix.md) | [report](049-usb-guard2-fix.report.md) |
| 050 | `usb-in-setup` | `b665baf` | PASS | [item](050-usb-in-setup.md) | [report](050-usb-in-setup.report.md) |

## Verbatim, and the redactions

### 000-029

The 60 files 000-029 are byte-for-byte copies of the verifier's inbox as of
2026-11-03 20:30 UTC (the concatenation's sha256 was checked against the
source after copying). The time can't be checked from the repository; the
hash pins the content. The 60 files concatenated in name order
(`cat evidence/verifier/0[0-2]?-*.md | sha256sum`, in the C locale) have
sha256 `f8293aa094267dad4f46266223bc03f0350f269abb1966b655f68721a568c561`,
in the inbox and as committed. Before committing they were searched for the
following, to be redacted:

| Searched for | Found | Redacted |
|---|---|---|
| tailnet hostnames (`*.ts.net`) | none | nothing |
| tailnet addresses (100.64.0.0/10) | none | nothing |
| private LAN addresses (192.168.x.x) | none | nothing |
| email addresses | none (the only `@` are OCaml/dune syntax such as `@wave_test/runtest`, and Python decorators) | nothing |
| the build machine's provider hostname | none | nothing |
| anything that looks like a credential or key (passwords, tokens, API keys, private-key blocks) | none | nothing |

So no file 000-029 differs from the verifier's own. Runner names (`runner1`)
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
| `030-docs-fix.md` | `0a8103e64534a4c5e6b089750e8e907b090c1b1965f4f04cd6f674220826009f` | same |
| `030-docs-fix.report.md` | `6a8860e161cd43785c7851f2564f529a05f12c7d61a1f410c947c544562dacf0` | `280a694e2e22f9f68781945c78751636aed06ba29a6e84464338ce34cb1bba3d` |
| `031-fabric-ping-fix.md` | `ae4e9c4a046ff43d2e8329e1364a1237a98f3973c19d57669532879eff0c1298` | same |
| `031-fabric-ping-fix.report.md` | `c825d3510855865a1a65017c507945ac818b290d85a0732bf91ac45573721864` | same |
| `032-usbfs-zlp.md` | `c20809f77bfe77e583acaee51f9c6d91cb4d862595c3b85ddf049cca3065446e` | same |
| `032-usbfs-zlp.report.md` | `0ce7c70eb17d6dbc6ceef6ea2ba8fb15c2d1abcc9772e00c362d5698a24dcd8d` | same |
| `033-docs-fix-fix.md` | `322c1f53f3bc982454db142832450cd0e23500edf9fbc68abc6fa2b33eca26da` | same |
| `033-docs-fix-fix.report.md` | `cd6b6b09827657c9814d09ae0c4528cb1ebc30b48c9d21854172dc9f991d4ac4` | same |
| `034-fabric-ping-fix2.md` | `b598638c437596a794e5aa12659dfb804d95b5a99c645d7d70d2bee1a905e0e0` | same |
| `034-fabric-ping-fix2.report.md` | `e948c21a33f60a4084f9769a3a30c492b7963fa937e4c89ac73bf0a0439c8e10` | same |
| `035-hostname-fix.md` | `014f5abcba3e3ca269ad0f6b35698122c8f4bb61cd8b4af8eb6f13f0ba599b26` | `00fa8b7524455cf53bb1973309c3d1475c3e0812a74269522679493fd13b0ff0` |
| `035-hostname-fix.report.md` | `c4cc402bbe16773a0dd025ed4dc1e5dfb354537d7dc1a9eaebfb4bcff6a3c426` | `97d079f019f99de0a47d8161966ad63ac161a6c23bf18dc9950ff12245d0cfb2` |
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
| `041-verifier-reports2.md` | `5a7b9b36cd97bf3475481d27b1dcc6d3238cfb154863a8bfe299235349336819` | same |
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
(`:130` and `:157`, the 000-029 and 030-038 tables, as `*.` and the domain;
`:235`, the 039-041 list, bare), and in one of `evidence/README.md` (`:24`,
its redaction summary). The two lines here are
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
| `042-verifier-reports2-fix.report.md` | `6d4e347f500e74c5047cdd40afd0a84423b8b79373c567119bb3f254782db76e` | `566ac7179f886a7cec705d9f900de1cae9eaadafd8a1dd41815ebf6cab325de2` |
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

Item 051 and later items and reports are not here.
