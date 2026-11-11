# 077 kv260-3core-fix

Branch `ar-kv260-3core`, two new commits on 076's tip `b3a019a` (base
`next-8x4` `d973462`; `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1).
These are the coordinator's requests after 076's PASS and the
orchestrator's board run.

Evidence levels:
- **RTL in FPGA fabric:** the orchestrator's run on the KV260, recorded
  here. I did not touch the board.
- **Model** and **stand-ins:** for the script changes.

No `src/`, `flow/` or `docs/methodology.md` change, no push, no SWD.

Commits:
- `76339d6` `fpga/kv260/board_smoke.py`: findings 1 and 4. Tests in
  `test/model/test_kv260_3core.py`, including finding 2's doc test.
- `36c6c26`:
  - the board log as evidence;
  - `fpga/kv260/README.md`: the board result, the new script order,
    finding 3's note and finding 2's commands;
  - `docs/hardware-day.md`: finding 2.

Tip **`36c6c26`**. `record_check.py --repo . --tree HEAD --commits
next-8x4..HEAD`: 746 files, 9 messages, 0 flagged.

The builder's claims:

1. **Board evidence.** `fpga/kv260/reports/v03-20261110-3c/board_smoke.log`
   is the orchestrator's log of `board_smoke.py` on the KV260.
   - **The bitstream:** the 3-core build from `38cf46e`, digest
     `a3b41f96`, built 2026-11-10 20:14:45 UTC. `protoemu.bit.bin` sha256
     is `ba67001fc2dbd21da1f72f6dbc704687214c5528ea0bbf617097847b35f6e8ca`,
     the same in runner1's `fpga-3c/build/out/` and in my local copy.
   - **Not quite verbatim.** I changed 2 lines: the local output
     directory's path is a scratch path that spells a home directory, so I
     wrote it as `<scratch>/out3c`. The original's sha256 is `54631a68…`,
     the committed file's `26efd6f3…`, and `diff` shows exactly those two
     lines.
   - **What README's board section says**, at the "RTL in FPGA fabric"
     level, read from the log:
     - load: protoemu48 was active; unloadapp printed `0 (Ok)`, loadapp
       `Loaded with slot_handle 0`, and listapps showed protoemu3c as the
       one active row;
     - PMOD0/1 zero before and after;
     - bringup ALL PASS: 49.9983 MHz, ID `0x50453303`, regs on 3 cores, the
       ring 0->1->2->0, the UART pins, quad, and crc32
       `0xDBA5645A`/`0x4F54EE58`/`0x981ADEAB`;
     - spim_test 12/12: quad 199.9/199.8 Mbit/s, 64-word 193.6; alt 183.6
       and 177.8, as in the v2 run;
     - v2_demo call, la, spi and i2c PASS.
   - **What it adds:** the run used the pre-fix script, with install before
     uptime/pgrep (the log's order shows it), after the orchestrator's
     manual preflight. The 2-core v0.3 bitstream has not been on the board.
     README's top paragraph and evidence-level line now say the same.
2. **Finding 1.** `Smoke.alive()` (`uptime`, then the pgrep check) now runs
   right after the local check, before `install()`. The `listapps` table
   (`preflight()`) stays after install. Tests:
   - `test_install_never_loads_and_comes_after_the_one_process_check`: the
     stand-in records install.sh in the same command list, so the order
     checked is `uptime`, pgrep, `<install.sh>`, `listapps`, `unloadapp`;
   - `test_a_busy_or_silent_board_gets_no_install[uptime|pgrep]`: refused,
     and install.sh is never called.
3. **Finding 4.**
   - `--phases install` alone stops after the read-only table with exit 0
     and prints "installed, not loaded"
     (`test_install_alone_stops_after_the_table`).
   - The pgrep pattern is now `'xmutil|fpga/kv260/host/'`, so a running
     xmutil refuses too
     (`test_a_running_board_tool_or_xmutil_refuses_before_the_load`, both
     cases). The shell's own pgrep line is still excluded by its `pgrep`
     word.
   - A timeout's message names that pattern
     (`test_a_timeout_names_xmutil_in_the_check_it_suggests`).
4. **Finding 2.** I fixed the docs rather than the default: `build.sh` can't
   know which app a build is for, and `install.sh`'s refusal already names
   `--cores`.
   - README: the 2-core build lines (`build.sh --cores 2`, and `build.sh
     --cores 2 25 && APP=protoemu48 ...`), the clock paragraph, and the USB
     section's 48 MHz build.
   - `docs/hardware-day.md:112`: `PMOD_DRIVE=12 fpga/kv260/build.sh --cores
     2 24`.
   - The 2026-10-07 record of the `fpga-kusb` build stays as it was run.
   - New `test_documented_build_commands_name_the_core_count`: no
     `build.sh <digit>` is left in either doc outside that record.
5. **Finding 3 (note only).** README now says that a rebuild from the same
   staged sources gives a different netlist, because `build_id.vh`'s HEAD
   and build time feed `pe_lab`. It cites 076's figures. No code change.
6. **Tests.** Model suite on runner1 at `36c6c26`: **3094 passed, 29
   skipped** (3088 at `0e4e6f0`; +6). I did not rerun the KV260 RTL
   simulation, because no simulated file changed: `board_smoke.py` and the
   docs are not in it.

Caveats:
- The xmutil success lines are now known for this board: `remove from slot
  0 returns: 0 (Ok)` and `protoemu3c: Loaded with slot_handle 0`. The
  stand-in's loadapp text (`loaded to slot 0`) differs, but the script
  doesn't match on it.
- The board result is one run, of one bitstream.

Asked checks:
- `diff` the scratch log (path in the coordinator's message) against the
  committed one: expect 2 lines, the path only, and the sha256s above.
- Read `board_smoke.py`'s `run()`: nothing should reach the board before
  `alive()` passes. Check the install-only exit too.
- Model suite (`remote_test.sh model`), including the new tests.
- Check README's board section against the log, line by line: it should
  claim nothing beyond the 3-core bitstream.
- Privacy: `record_check.py` as above, and a grep of the log for home paths,
  host names and addresses.

No Kria, no SWD; use `--ignore=test/model/test_swd.py` on model runs.
