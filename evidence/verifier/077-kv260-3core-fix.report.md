VERDICT: PASS

077 kv260-3core-fix (branch `ar-kv260-3core`, `76339d6` and `36c6c26` on `b3a019a`; backlog AR): verifier report.
Kria: verifier does not touch it. No SWD.

The fixes for 076 findings 1, 2 and 4 are in:
- `board_smoke.py` now reaches the board only after `uptime` and the pgrep check pass;
- `--phases install` alone exits 0;
- the pgrep pattern includes xmutil;
- the 2-core build commands name `--cores 2`.

The new tests catch four mutants of the fix.

The committed board log equals the orchestrator's raw log apart from the two path lines, with the claimed sha256s. The README's board section claims nothing the log doesn't show, apart from one detail (finding 2), and it claims nothing beyond the 3-core bitstream.

Privacy: the committed log and everything this branch adds are clean of every home-path form I searched for (below). Neither checker in use covers home paths (finding 1).

Evidence levels: RTL in FPGA fabric (the orchestrator's run; I read only its log), plus model and stand-ins for the script changes.

## Findings

1. **No automated check covers home paths. `record_check.py`'s silence on the raw log is by design, not a pass.**
   - Its docstring says it does not catch "private strings other than these three values (home paths, addresses, SSIDs and the like): run the repo's privacy scan beside this checker".
   - On scratch files, it gives "TOTAL flagged: 0" for all three forms: a plain `/home/<user>/` path, the dashed `-home-<user>-` form, and the full scratch form `/tmp/claude-<uid>/-home-<user>/…`. So does my own old-id checker.
   - The repo's own scan, `test/model/test_private_hosts.py`, covers tailnet labels, MagicDNS names and tailnet addresses only.
   - So the home-path redaction of the raw log, here and in every evidence file, rests on manual searches like mine below.
   - Suggestion (for the orchestrator or item AL): a home-path class in `test_private_hosts.py`, or in a sibling test. Match `/home/<name>` and `-home-<name>` (the scratch directory form), `/Users/` and `claude-<uid>` scratch paths. Allowlist the generic forms already on public main (below).
2. **Minor: README says "the UART ring 0 -> 1 -> 2 -> 0 at 115200", but the 115200 is not in the log.**
   - It is the code's rate (`bringup_kria.py` `step_loopback`, `uart_cfg(ctx, 115200)`), so it is true of this run.
   - The section is introduced with "From the log", though. The log shows only the three pairs and their messages (`board_smoke.log:176-186`).
3. **Minor: the new doc test checks only a clock right after `build.sh`.**
   - `test_documented_build_commands_name_the_core_count` matches `build\.sh\s+\d`. A bare `fpga/kv260/build.sh` before a 2-core install, 076's first case, would pass it.
   - The docs are fixed now: the only `build.sh` mentions without `--cores` are prose (`README.md:61`, `:66`, `:175`) and the 2026-10-07 record (`:1766`).
4. **Note: the new pgrep pattern has not run on the board yet.**
   - The logged run used the pre-fix script: `pgrep -af fpga/kv260/host/ || true` comes after install (`board_smoke.log:5-19`).
   - On the next run, `'xmutil|fpga/kv260/host/'` refuses (fail-closed) if any process with `xmutil` in its command line is resident.
   - The log also shows the board's pgrep output contains its own `bash -c pgrep …` line (`:18`). The script excludes that line by its `pgrep` word, as before.

## Asked checks

- **Raw log against the committed log.**
  - The orchestrator's scratch log (read-only; not quoted) has sha256 `54631a68e4b27eb0…` and 313 lines.
  - The committed `fpga/kv260/reports/v03-20261110-3c/board_smoke.log` has sha256 `26efd6f35bf82d21…` and 313 lines. Both match the claims.
  - `diff` gives exactly lines 2 and 6, where the scratch output directory became `<scratch>/out3c`. ✓
- **The bitstream.**
  - `protoemu.bit.bin` sha256 is `ba67001fc2dbd21d…`, the same in the orchestrator's local copy and in runner1's `ar-kv260-3core/fpga-3c/build/out/` (read-only).
  - The local `build_info.json` says HEAD `38cf46e`, digest `a3b41f96`, 3 cores, clk_div 24, 2026-11-10 20:14:45.
  - On the board, bringup's build step read back the same identity from pe_lab (`board_smoke.log:158-160`): "built 2026-11-10 20:14:45 UTC from HEAD 38cf46ef67e8, RTL digest a3b41f96, clk_div 24", matching `expected_build.json`. So the loaded bitstream is the 3-core build. ✓
- **`board_smoke.py`'s `run()` at `36c6c26`.**
  - The order is `check()` (local only: `build_id.py info` and the record) → `alive()` (`uptime`, then `pgrep -af 'xmutil|fpga/kv260/host/' || true`) → `install()` → `preflight()` (listapps) → `load()` / `listapps()` → `smoke()`. Nothing reaches the board before `alive()` passes. ✓
  - **Install-only:** after the read-only table, it prints "installed, not loaded" and returns 0. With `--phases install,smoke` and no load, the strict `listapps()` check still runs before any test. ✓
  - The timeout message names the new pattern. The load-command rules (each read before the next, error lines, the one active row) are unchanged from 076.
- **New tests and mutants** (`smut.sh`, runner1, `test_kv260_3core.py` at `36c6c26`):
  - unmutated: "73 passed";
  - `alive()` moved after install: 4 failed (the order test, both busy/silent cases, install-alone);
  - the pgrep pattern back to the host path only: 6 failed (including both running-process cases and the timeout message);
  - install-only back to the strict listapps check: 1 failed (`test_install_alone_stops_after_the_table`);
  - `alive()` removed: 8 failed.
- **Model suite** at `36c6c26` (my runner1 clone; `remote_test.sh`'s pytest flags but `-n 8`, with `--ignore=test/model/test_swd.py`): rc 0, "3094 passed, 29 skipped in 872.23s (0:14:32)", as claimed. SIGROK checks skipped: 28, and a pass says nothing about them.
- **README's board section against the log, line by line:**
  - **Load:** protoemu48 active (`:34`, `:81`); "remove from slot 0 returns: 0 (Ok)" (`:83`); "protoemu3c: Loaded with slot_handle 0" (`:86`); protoemu3c the one active row (`:147`, `:149`). ✓
  - **PMOD:** PMOD0 and PMOD1 `00000000` before and after (`:152`, `:310`). ✓
  - **Bringup** (`:155-203`):
    - identity matches; clock 49.9983 MHz; ID `0x50453303`, "v0.3, 3 cores, 3 banks";
    - 200 rounds on all 3 cores; pins;
    - the ring 0→1, 1→2, 2→0 (its 115200 is finding 2);
    - uart_tx_pin and uart_rx_pin at 9600; quad ID;
    - crc32 `0xDBA5645A` / `0x4F54EE58` / `0x981ADEAB`, each equal to its expected value;
    - ALL PASS. ✓
  - **spim_test** (`:205-256`): 12 configurations (shared and alt × mclk/4, /2, /1 × 1-bit and quad), all PASS, R0..R3 ×3 cores. Quad at SCK = clk 199.9 / 199.8 Mbit/s and 64-word 193.6 (529 clk); alt 183.6 and 177.8 (576 clk). The same figures and clock counts are in the v2 tables (`README.md:1199`, `:1205`, `:1210-1212`). ✓
  - **v2_demo** (`:259-306`):
    - call: registers equal the model's;
    - la: one capture − drive offset `[58837]` for all 160 edges, and async 240 edges all captured;
    - spi: no bypass correct up to DELAY 5, SYNC_BYPASS up to 13;
    - i2c: EEPROM answers;
    - all PASS. ✓
  - **Order of the run:** the log shows install before `uptime`/`pgrep` (`:5-19`), as the README says. "The orchestrator read `uptime`, `pgrep` and `listapps` by hand first" is not in the log. It is the orchestrator's statement, and I can't check it.
  - **Scope:** every board claim is for the 3-core bitstream. The top paragraph, the evidence-level line and the section's last line all say the 2-core v0.3 bitstream has not been on the board. ✓
- **Finding 2's docs.**
  - `README.md:47`, `:130`, `:133` and `:1321` now carry `--cores 2`, and so does `docs/hardware-day.md:112`.
  - The 2026-10-07 `fpga-kusb` record (`:1766`) is kept as run.
- **Finding 3's note** (`README.md:271-276`) quotes 076's figures correctly.
- **Privacy and home paths.**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `36c6c26`: "tree HEAD: 746 files checked", "commit messages checked: 9", "TOTAL flagged: 0". Mind finding 1's scope, though.
  - All 9 commits are by the noreply identity with the trailer. `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
  - **The forms I searched:**
    - `/home/<any>`, `-home-<any>`, `home/<user>`, `home-<user>`, `~<user>`;
    - `/Users/`, `C:\Users`, `%2Fhome`, `/root/`, `/var/home`;
    - `/tmp/claude-`, `claude-<uid>`, `.claude/projects`;
    - the user name outside the GitHub login;
    - IPv4 dotted quads, the tailnet classes (`ts.net`, `tail<hex>`, `fd7a:`, 100.64/10), `ubuntu@` and the host names.
  - **The committed log:** 0 hits for all of them. The one raw match was `bringup_kria.py` containing "kria.".
  - **What the branch adds** (`git diff d973462 36c6c26`, added lines, and the 9 messages): 0 hits.
  - **The whole tree at `36c6c26`:** 21 files with `/home/` or `-home-` forms, all generic and none the user's own: `/home/ubuntu` (the board's user), `/home/pi`, `/home/runner1`, `/home/someone`, `/home/<other>`, `/home/<other>`, `/home/<other>`. The file set and the per-file counts equal public main `d2ed344`'s exactly, so this branch adds none.
  - The listapps table in the log names other KV260 apps installed on the board, `kv260-q*` and the like. They are not on the private list, and `test/model/listapps-20261103.txt` already publishes the board's table in the same form.
- **Scope.** No `src/`, `flow/`, `docs/methodology.md` or `tools/` change since `b3a019a`. No simulated file changed, so not rerunning the KV260 simulation is right.

## What I ran

- **runner1** (`~/protoemu-flow/verifier-077/`, my clone at `36c6c26`; no harden, no Vivado):
  - the model suite (3094 passed, 29 skipped);
  - `smut.sh`;
  - `sha256sum` of AR's bitstream (read-only).
- **Locally:**
  - the diffs;
  - the log `diff` and hashes, and the orchestrator's local copy, all read-only;
  - the home-path searches;
  - `record_check.py` and my checker on planted scratch files;
  - the ancestry check.
- **No Kria.**

Summary: 076's findings are fixed and guarded by tests that catch every mutant I tried. The 3-core bitstream passed every smoke step on the KV260 per the committed log, which equals the raw log apart from the two redacted path lines. The branch is clean of home paths, but only manual searches show it: no checker covers that class (finding 1).
