# Verifier reports

Every report of the project's independent verifier, with the item it
answered, from the first (2026-11-02) to item 038 (2026-11-04): 39 items, 39
reports, 19 `VERDICT: PASS` and 20 `VERDICT: FAIL`. FAILs are kept: they are
most of what the verifier caught.

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

Commits named here are local commits of the project; some are on branches not
merged into this one (for example `usbfs-zlp`).

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

## Verbatim, and the redactions

### 000-029

The 60 files 000-029 are byte-for-byte copies of the verifier's inbox as of
2026-11-03 20:30 UTC (the concatenation's sha256 was checked against the
source after copying). Before committing they were searched for the
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
then searched for the following, to be redacted:

| Searched for | Found | Redacted |
|---|---|---|
| the Kria's tailnet name or its bare label, in any case, with or without `ubuntu@` | 7 lines of `035-hostname-fix`, which quote the `git grep` needle and an upper-case plant | all 7 |
| other tailnet hostnames (`*.ts.net`) and addresses (100.64.0.0/10) | none (both named only as classes to search for) | nothing |
| private LAN addresses (192.168.x.x) | none (named only as a class; `10.77.0.x` is the test's own network namespace) | nothing |
| the local user's home directory (`/home/` and the user name) | 4 lines in 035 and 036, where reports quote what was searched for | all 4 |
| the build machine's hostname | 2 lines in 035, and inside the two email addresses below | all |
| email addresses | 2 lines in 035 with the commit author's address. Kept: `modprobe@loop.service` (a systemd unit); the public organisational `forum@t13.org` and `asic-competition@janestreet.com`; and addresses on reserved or placeholder names that reach no one, `root@example.org`, `n@cocotb.test`, `ubuntu@kria.example`, `ubuntu@kria.local` | the 2 |
| MAC addresses other than the project's made-up locally administered ones | 1, the Kria's Ethernet MAC, quoted in report 030. Kept: `02:00:de:ad:be:01` and the truncated `02:57:4c:…` (035), both locally administered | the 1 |
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

That is 17 replacements on 16 lines of 4 files; the other 14 files are
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

Items 039 and 040 have no report yet and are not here, nor is any later
report.
