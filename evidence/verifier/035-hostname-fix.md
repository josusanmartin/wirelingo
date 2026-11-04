Commit: 2650f98  Base: dbde7f7 (improve-2026-11)  Branch: hostname-fix  (backlog U, from your 033 note)
Item: remove the author's private tailnet hostname from tracked files; sweep for other private identifiers.
Builder's claims:
1. `git grep [tailnet label redacted]` finds nothing. docs/hardware-day.md (7) and fpga/kv260/README.md (17) use `ubuntu@<kria-host>`; README says once what it stands for and that install.sh reads WL_KRIA.
2. install.sh: host is ${WL_KRIA:-${KRIA:-}}; with neither set it exits 1 before anything else with "set WL_KRIA to the board's ssh address, e.g. WL_KRIA=ubuntu@kria.example"; otherwise unchanged. legacy_demo.py and usb_replay_demo.py only had the name in their usage docstring (they run on the Kria), now `ubuntu@<kria-host>`; --help output unchanged.
3. [home directory redacted] paths removed from docs/blind-test.md, rmii-study.md, post-freeze-protocols.md, release-evidence.md (3), team-integration-checklist.md (three broken links outside the repo now plain ~/ paths marked outside the repository).
4. New test/model/test_private_hosts.py: every `git ls-files` file (evidence/ included) must not contain the tailnet name; with no .git (mutants.py copies) it falls back to walking the tree minus .venv/__pycache__/sim_build (orchestrator accepts the fallback); a second test plants the name in a scratch repo (tracked reported, untracked not, cleared on removal). Mutant privacy-kria-default-host killed.
5. Planted hit in docs/blind-test.md: 1 failed with docs/blind-test.md:368; reverted: 2 passed.
6. Sweep, not fixed: test/gl.log (tracked gate-level log) has 75 [home directory redacted] paths. Clean: [build machine hostname redacted]/[build host provider redacted], other *.ts.net or 100.64/10, 192.168, SSIDs, private emails, real MACs. methodology.md 1-36 untouched.
7. Model suite 2353 passed, 29 skipped.
Kria: not touched; nothing is hardware. No SWD. Also check: the sweep's claim of "clean" for the listed classes, and that no script behaviour other than the host source changed.
