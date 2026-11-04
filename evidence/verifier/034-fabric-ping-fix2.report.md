VERDICT: FAIL

Commit c4ea377 on base 97e442d (an ancestor). It follows 48fcce0 (report 031) with two commits:
- f86eb34: code, tests, mutants;
- c4ea377: the doc counts.

The change is `fpga/kv260/host/eth_tap.py`, `fabric_eth.py`, `test/model/test_fabric_ping.py`, `test/mutants.py` and `docs/eth10-network.md`. Nothing in `src/`, `flow/` or `firmware/` changed.

**What holds:**
- 031's finding is fixed for the window it named: an end of any kind inside `FabricEth`'s constructor now gives the board back.
- 031's note 2 is fixed: `give_back` goes on when the SEL read fails.
- Note 3 is right.
- The new tests fail on the 031 code and pass on this one. The two new mutants are killed.
- The suite and mutant counts are as claimed.

**Why it fails:**
1. The same kind of window is still open, one statement later, in the rest of `FpgaBackend.__init__` after its try. A Ctrl-C or SIGTERM there leaves the host port on the fabric master with quad on, and `run_closing` prints "nothing was left on the board". The item's claim 1, `run_closing`'s docstring and the doc's "given back on any end" all say otherwise.

| Claim | Result |
|---|---|
| 1. `FpgaBackend.__init__` closes the rig on any BaseException, with signals ignored during the close; new test; mutant `fab-backend-ctrl-c-kept` | True for `FabricEth(...)`. **Not for the rest of `__init__`** (finding 1) |
| 2. Note 2: SEL read inside `step()`; test; mutant `fab-give-back-sel-read-unguarded` | PASS |
| 3. Note 1: a Ctrl-C at the ID read is given back | PASS. The test also passes on the 031 code, where `board_rig` already caught it, so it adds coverage, not a fix |
| 4. Note 3: the sampler is on core 0; the other "core 1" mentions are the sender | PASS |
| 5. `docs/eth10-network.md` covers the window and cites 029 and 031 | It cites them, but "The board is given back on any end" (`:363`) is still not true (finding 1) |
| 6. Model suite 2376 passed, 28 skipped; mutants 101/101; model probes; logs on runner1 | PASS: reproduced |

## Blocking

1. **A Ctrl-C or SIGTERM after `FabricEth` is built, but before `FpgaBackend` reaches `run_closing`, still leaves the board on the fabric path.**
   - **Where.** The try at `fpga/kv260/host/eth_tap.py:234-238` covers only `self.fab = fabric_eth.FabricEth(rig, clock_hz)`. Several steps then run with the rig whole and nothing holding it:
     - lines 239-241 (`eth.Station(eth.mac(CHIP_MAC_TEXT), ipaddr)`, the lists, `corrupt`);
     - the return from `__init__`;
     - the return from `make()` (`:728-729`).

     `board_rig`'s try has exited, and so has `FpgaBackend`'s. `run_closing`'s `backend` is still `None` (`:658-660`), so its `finally` (`:673`) closes nothing, and `:663` prints "nothing was left on the board".
   - **How I showed it.** `test_v034_window.py` ran in a `git archive` copy of c4ea377 on runner1:
     - it runs `eth_tap.main(["--backend","fpga","--probe"])` on the item's fake KV260 (`fake_parts`, `sentinels`);
     - it lets the real `FabricEth.__init__` finish;
     - it then faults the first `eth.mac` or `eth.Station` call after it, with a Ctrl-C (`KeyboardInterrupt`) or a real `os.kill(SIGTERM)`.

     4 of 4 fail:
     ```
     ctrl-c@mac: quad at build [1]; SEL writes [1]; hostcfg 1; lab mode [0]
     sigterm@mac: quad at build [1]; SEL writes [1]; hostcfg 1; lab mode [0]
       stopped by signal 15; nothing was left on the board  (rc 143)
     sigterm@Station: quad at build [1]; SEL writes [1]; hostcfg 1; lab mode [0]
     ```
     SEL is written 1 and never 0, HOSTCFG's QUAD_EN stays 1, and the SIGTERM run says nothing was left on the board.
   - **The test hits that window and no other.** I made a second copy with `self.station = …` moved inside the try. There the same file gives `5 passed`, with SEL writes `[1, 0]` and hostcfg 0.
   - **Size.** `eth.Station(eth.mac(…))` takes 3.4 µs on runner1 (Python 3.12.3). The window is that plus the two returns: about a thousandth of 031's 3.3 ms, and likely a few times longer on the Kria's A53. It is small, but it is the class of window 029 and 031 were about, and the item, the docstring and the doc say it is closed.
   - **Fix.** Give the rig an owner that outlives `make()`, rather than moving the try down a line. For example, `board_rig` or `FpgaBackend` puts the rig in a holder that `run_closing`'s `finally` closes when `backend` is still `None`. Putting all of `__init__` after `board_rig` inside the try is smaller, but leaves the returns from `__init__` and `make()`. Add a test that faults after `FabricEth` returns, like mine.

## Checked

**Claim 1 (`eth_tap.py:236-238`).**
- `except BaseException: fabric_eth.shielded(rig.close); raise`. `shielded` (`fabric_eth.py:370-379`) sets SIG_IGN on SIGINT, SIGTERM and SIGHUP, and restores the old handlers in a `finally`. `give_back` nested inside it does the same and restores SIG_IGN, so the nesting is sound.
- **The new test** (`test_a_backend_stopped_while_fabric_eth_is_built_gives_the_board_back`) faults the first `asm.assemble` in `FabricEth.__init__`. It asserts quad was 1 at the fault, and afterwards SEL 0 last, every other logged write 0, HOSTCFG 0 and pe_lab in GPIO mode.
- **Against the 031 code** (48fcce0 with c4ea377's test file): `[ctrl-c]` fails. `[sigterm]` and `[sighup]` pass, as they should: `SessionSignal` is an `Exception` there.
- **Mutant** `fab-backend-ctrl-c-kept` is killed by `…[ctrl-c]`.

**Claim 2 (`fabric_eth.py:341-367`).**
- The SEL read is now inside `quad_off`, run by `step()`.
- At 48fcce0, `test_give_back_goes_on_when_the_select_cannot_be_read` fails (SEL left at 1). At c4ea377 it passes.
- `fab-give-back-sel-read-unguarded` is killed by it.
- `fab-give-back-leaves-quad` now matches `spim.write(0x80B, 0)` and is still killed (by `test_a_rig_stopped…[ctrl-c]`).

**Claim 3.**
- `test_a_ctrl_c_at_the_id_read_gives_the_board_back` raises at the chip's `A_ID` read through `FakeSpimMem.on_id`, which is after `select(fabric=True)` and before the clock measurement.
- It passes at both commits, because `board_rig`'s `except BaseException` covered it already. 031's note 1 was about coverage, so that is enough.

**Claim 4.**
- `docs/eth10-network.md:21` and `:320-321` and `fabric_eth.py:20` and `:28-30` give `eth10_tx_cs` on core 1 and `usb_sampler` on core 0.
- `eth_tap.py:555` and `:592` and doc `:339` are core 1's divider.
- The `evidence/fabric-ping-20261103/README.md` copy is not on this branch, as the item says.

**Claim 6.**
- My stock run: 2376 passed, 28 skipped.
- My mutant run of `'hw-*' 'fab-*'`: 101 of 101 killed (22 `fab-`, 79 `hw-`).
- `--backend fpga-model --probe` at 50 and 40 MHz: `probe PASS` both.
- The builder's logs in `runner1:~/protoemu-flow/agent-runs/fabric-ping/logs/f86eb34/` show `101 of 101 mutants killed`, `probe PASS` in `probe-50.log` and `probe-40.log`, and `"pass": true` in both selftest logs. I did not rerun the selftests: they need the TAP namespace.

**Board.** There is no new board run. The change is to the end and error paths, which the 031 addendum's smoke run doesn't exercise either way. The doc's "The board runs so far went through both windows without a fault" is a statement about past runs, and it is correct.

## Notes

1. **A seam inside `board_rig` is not shown.** It lies between `board_rig`'s try and the caller. On CPython 3.10, a signal that arrives after the last call inside `board_rig`'s try is handled at the eval-breaker check after `CALL_FUNCTION` returns, which is outside both trys. That is sub-microsecond, and on 3.12 the next check falls inside `FabricEth.__init__`. I read this from CPython and did not demonstrate it. The owner-based fix above closes it too.
2. **The ID-read test's docstring** says "Verifier report 031, note 1". It tests a path that was already given back at 48fcce0. That is not a problem; it is the coverage 031 asked for.

## What I ran

**On runner1:**
- `remote_test.sh model` at c4ea377: `model rc=0`, `2376 passed, 28 skipped in 800.34s (0:13:20)` (checkout `agent-runs/protoemu-verify-c4ea377-1315756`, removed).
- In `~/verifier-034/`:
  - `test_v034_window.py` in a `git archive` copy of c4ea377: `4 failed, 1 passed` (the pass is the timing test, `3.39 us`); the same file in a copy with the station built inside the try: `5 passed`;
  - c4ea377's `test_fabric_ping.py` on a 48fcce0 copy: `2 failed, 52 passed` (`…[ctrl-c]`, `…select_cannot_be_read`);
  - `python test/mutants.py 'hw-*' 'fab-*'` in a clone at c4ea377: `101 of 101 mutants killed`;
  - `eth_tap.py --backend fpga-model --probe --clock 50` and `--clock 40`: `probe PASS`, `probe PASS`.
- Read only: the builder's logs under `agent-runs/fabric-ping/logs/f86eb34/`.

**Locally:** the diff against 48fcce0 and 97e442d.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
