# Verifier reports

Every report of the project's independent verifier, with the item it
answered, from the first (2026-11-02) to item 029 (2026-11-03): 30 items, 30
reports, 15 `VERDICT: PASS` and 15 `VERDICT: FAIL`. FAILs are kept: they are
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
merged into this one (for example `fabric-ping`).

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

## Verbatim, and the redactions

The 60 files are byte-for-byte copies of the verifier's inbox as of
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

So no file here differs from the verifier's own. Runner names (`runner1`) and
paths under `~/protoemu-flow` are kept, as are home-directory paths, which
name only the local user.

Reports written after 029 are not here yet.
