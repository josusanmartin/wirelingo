# 058 tidy

Branch `tidy`, commits `9e8553a`, `bae67a3`, `5132d29`, `e95f0de` and `bbf8b11` on `e449e40`. Backlog item AG: a behaviour-neutral tidy of the code before the repo goes public.

28 files change (+15 −153). Nothing in `src/`, `flow/`, `docs/`, `evidence/`, `README.md`, `firmware/` or `ocaml/` is touched.

The builder's claims to check:

1. **`9e8553a` removes `test/gl.log`.** It is a 2026-10-01 gate-level make log with 75 home-directory paths, the open item from verifier 035. Nothing reads it, and `.gitignore` already ignores `test/*.log`.
2. **`bae67a3` removes unused imports in `tools/`:**
   - `pe/ethhost.py` (`sim`), and the same line in its generated copy `tools/micropython/eth10_host.py`, so `mpy_bundle --check` still passes;
   - `pe/modelhost.py` (`host`), `pe/postfreeze3.py` (`Player`), `pe/timing.py` (`OP_SET`, `OP_PIN`, `OP_FLAG`).

   Nothing imports these names through these modules.
3. **`5132d29` removes unused imports** in 15 test modules and `test/dbg_trace.py`. No removed name starts with `test_`, so pytest collects the same tests.
4. **`e95f0de` removes locals assigned and never read:**
   - test_line `out`, test_nec `t_out`, test_bench_fw `t0`;
   - test_micropython_driver `level, bits, t_edge`;
   - test_uart_capture_host `active_row` (2 lines).
5. **`bbf8b11` drops two unused imports:** `hw_day.py` `import os`, and `spi_uart_demo.py` `import math`.
6. **Guards.**
   - `uartcapture_peer.decode_capture` is kept, because a test checks it by identity.
   - All 234 mutant sites still match exactly once.
   - The file:line citations in docs and evidence still point at the right lines: `test_post_freeze.py:395/:403`, `ethhost.py:36`, `legacy_demo.py:1347`, and the postfreeze_demo and v2_demo cites. The verifier's `hw_day.py` line cites were already out of date and now shift by one more line.
   - The release receipts are tied to their own validation checkouts, so they are unaffected.
7. **Firmware.** All 72 `.pasm` files assemble byte-identical at e449e40 and at bbf8b11, by two independent hashers:
   - digest `f631ff78…26ea89`;
   - `fwhash_r2.py` total `d7cd58e2…cadad`.

   `protoemu_fw.py` is identical too (`708bb344…`).
8. **Regression at bbf8b11** (runner1, `~/protoemu-flow/tidy-bbf8b11/`). Every count matches e449e40's:
   - model 2878 passed, 29 skipped;
   - test 27/27, rmii_rtl 6/6, bench_fw 5 + 1 skip, eth_stack_rtl 7/7, post_freeze 16/16;
   - pps 4 + 1 skip, blind 17/17, eth_reply_rtl 2/2, sigrok 8 skip, lockstep 6/6.

   The combined run's model step hit `remote_test.sh`'s 30-minute limit at about 98% under load, with no failures. The model suite then passed on its own in 17 minutes.
9. **Mutants: 234 of 234 killed.** That is 216 pytest mutants, and 18 cocotb/lockstep mutants each over a 6/6 passing baseline. None survived, went stale or ended in an infrastructure error.
10. **Left alone on purpose,** with the builder's reasons:
    - `librelane_plugin_protoemu.py` (byte identity is cited in release-evidence.md:206);
    - `formal/lean/check_rtl_rx_autopush.py` (its receipts record its digest);
    - `ocaml/test/run_parity.py`;
    - the unused imports in `flow/`;
    - the preloads in `legacy_demo.py` and `spi_uart_demo.py`;
    - style-only items in the board demos;
    - `test/dbg_trace.py`, `syn/area*.ys`, `test/tb.gtkw`.

Asked checks:
- **Diff `e449e40..bbf8b11` line by line.** Is every removal certainly dead?
  - Look for side-effect imports, names re-exported or used by `getattr`, `importlib`, `from X import *`, mutant patterns or string lookups, and names cited by docs or evidence.
- **Firmware:** rerun one of the hashers at both commits.
- **Tests:** rerun the model suite at bbf8b11 with `--ignore=test/model/test_swd.py`, and rerun `mutants.py` on any mutant whose file changed.
- **Privacy:** the repo is going public, so check the tree at bbf8b11 for private strings, beyond what the builder lists:
  - The builder reports that `test/model/test_private_hosts.py:22,58,60,61` and `test/mutants.py:519` hold the real tailnet label as split string literals, which anyone can join. That fix is a test change, so it goes to a separate item.
  - Confirm the finding, and say whether anything else in the tree is like it.

The usual rules: no SWD, no Kria.
