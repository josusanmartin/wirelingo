VERDICT: FAIL

Commit 48fcce0, parent 86fe42d, and da82a51 (029) is an ancestor. The changes are `eth_tap.py`, `fabric_eth.py`, `test_fabric_ping.py`, `mutants.py` and `docs/eth10-network.md`.

**What holds:**
- 029's finding 2 is fixed.
- 029's finding 1 is fixed for the window it named, and my 029 attack now ends with the board given back.
- The notes are dealt with.
- The model suite, the 99 mutants and both board files reproduce.

**Why it fails.** One narrower gap of the same kind remains. A Ctrl-C after `board_rig` has returned, while `FpgaBackend` builds `FabricEth`, still leaves the host port on the fabric master with quad on. So "the board is given back on any end … Ctrl-C" (`docs/eth10-network.md:363`) is still false. The window is milliseconds, and the fix is one word.

| Claim | Result |
|---|---|
| 1. Handlers before the build; `board_rig` gives back a half-built rig; five faults tested; three mutants | PASS for `board_rig` and `run_closing`. **FAIL** for "any end" (finding 1) |
| 2. Link pulses compared per phase; old model files refused; probe needs one 100 ns pulse; two mutants | PASS |
| 3. Zero-word blind spot documented; `--pcap` reported and never in the pass; `86fe42d` wording | PASS |
| 4. Model 2370 passed, 28 skipped; 99/99 mutants; 029 board file SAME against a fresh model (phases 1, 4, 0; pulse `[[5,5]]`) | PASS, all reproduced |
| Addendum: board smoke run of 48fcce0 | PASS: archive hash, probe and `--compare` SAME reproduced from `evidence/031/` |

## Blocking

1. **Ctrl-C while `FabricEth` is built is not given back.** `FpgaBackend.__init__` (`eth_tap.py:233-238`) does this:
   ```
   rig = fabric_eth.board_rig(clock_hz) if rig is None else rig
   try:
       self.fab = fabric_eth.FabricEth(rig, clock_hz)
   except Exception:
       rig.close()
       raise
   ```
   - `KeyboardInterrupt` is not an `Exception`, so the rig is not closed.
   - `run_closing` never gets a backend, so its `finally` closes nothing (`if backend is not None`).
   - The rig is fully built at this point: SEL = fabric, quad on.
   - **How I showed it.** My `window2.py` goes through `eth_tap.main(["--backend", "fpga", "--probe"])` on `test_fabric_ping`'s fake KV260 (`fake_parts`, guard stubbed to `protoemu`), with a fault in the first `asm.assemble` call inside `FabricEth.__init__`:
     ```
     Ctrl-C while FabricEth assembles its programs (rig already built): main raised KeyboardInterrupt; SEL writes [1] (ends fabric); quad (model HOSTCFG) 1
     SIGHUP while FabricEth assembles its programs: main returned 129; SEL writes [1, 0] (ends GPIO); quad (model HOSTCFG) 0
     exception while FabricEth assembles its programs: main raised OSError; SEL writes [1, 0] (ends GPIO); quad (model HOSTCFG) 0
     ```
     SIGHUP and an exception are handled because `SessionSignal` is an `Exception`; only Ctrl-C gets through.
   - **Size of the window.** `FabricEth(...)` takes 3.3 ms on runner1, probably some tens of ms on the Kria's A53. That is narrow, but it is the same class as 029's finding 1, and the doc's claim covers it.
   - **Fix:** `except BaseException:` there. Add a `FAULTS`-style case that interrupts `FabricEth.__init__` through `eth_tap.main`, and a mutant that puts `Exception` back.

## Checked

**Finding 1 of 029, the window it named.** I re-ran 029's three cases on the new code, through `eth_tap.main`, with the fault inside HwRig's 2 s clock measurement:

| Fault | Result | SEL writes | Quad | pe_lab |
|---|---|---|---|---|
| Ctrl-C | `KeyboardInterrupt` | `[1, 0]` (ends GPIO) | 0 | GPIO |
| `OSError` | `OSError` | `[1, 0]` | 0 | GPIO |
| SIGHUP | rc 129 | `[1, 0]` | 0 | GPIO |

In 029 all three ended on the fabric path.

**How the fix works:**
- `run_closing` (`eth_tap.py:640`) installs the SIGTERM/SIGHUP handlers before `make()`, and returns 2 on `NoActiveApp`.
- `board_rig` (`fabric_eth.py:370-389`) catches `BaseException` and calls `give_back`, then re-raises.
- `give_back` (`:341`) ignores SIGINT, SIGTERM and SIGHUP while it runs. It tries every step and raises the first error.
- `give_back` reads SEL to decide whether quad can be turned off over the fabric master. SEL does read back on the real RTL: `pe_spim.v:110`, `6'd3: reg_rd = {30'd0, sel_alt, sel_fabric}`.
- Its steps match `HwRig.close` (`postfreeze_demo.py:150-162`), less the core and pin registers. No core has been started during the build.

**The build now differs slightly from `v2_demo.Hw()`'s own order.** `board_parts()` builds `Spim()` before `board.reset()` (which reads only `VERSION`), and `board.reset()` now comes before `lab.mode(GPIO)`. I see no effect on the chip. The orchestrator's smoke run went through this path on the board.

**The new test is real.** `test_a_rig_stopped_while_it_is_built_gives_the_board_back` asserts, for each of the five faults:
- that quad was on when the fault hit (`quad_at_fault == 1`, from the model's `hostcfg`, since HOSTCFG reads 0 on the bus);
- the end state: SEL back on the GPIO path, quad off, pe_lab in GPIO mode, PMOD writes all 0.

The three new mutants (`fab-give-back-leaves-quad`, `fab-half-built-rig-kept`, `fab-rig-built-before-handlers`) are each killed.

**Finding 2 of 029.**
- `td_record` (`eth_tap.py:584`) is the link pulses plus the frame's edges. The model keeps one per divider phase (`tx_phases`), and a board run must equal one of them whole.
- My 029 copy of the board file with two extra pulses now gives **DIFFERENT** against a fresh model: "link pulses there `[[5, 5]] / [[5, 5], [2000, 5], [4000, 5]]`".
- 029's own model file is refused: "a model file without link pulses per phase …", DIFFERENT.
- `link_pulse_ok` (`:577`) makes the probe require exactly one 100 ns pulse before the frame. `fab-compare-blind-to-pulses` and `fab-probe-ignores-pulse` are killed.
- The doc now says what is compared and that nothing after `TAIL_S` is seen.

**Notes 2-4 of 029.**
- `eth10-network.md:312-317` states the zero-word blind spot, with the 6 / 2 counts.
- The `--pcap` result reports the file size, tcpdump's exit status and its stderr. None of it enters `ok`; the only keys checked are the `no_reply_*` ones.
- `86fe42d` → `48fcce0` changes only the doc and the test (`git diff --stat`). So "`eth_tap.py` and `fabric_eth.py` are this commit's" is true.
- The logs the doc names are on runner1: `agent-runs/fabric-ping/logs/86fe42d/` holds `probe-*`, `selftest-*` and `mutants.log`, and `logs/48fcce0/mutants.log` ends "99 of 99 mutants killed".

**Claim 4.**
- I made a fresh `--backend fpga-model --probe --save` in my clone: probe PASS.
- `--compare` of `evidence/029/fabric-ping-b.json` (same sha256 as the file I used) against it: arp `[[5, 5]]` + 947 edges at phase 1, ping `[[5, 5]]` + 939 at phase 4, udp `[[5, 5]]` + 917 at phase 0. **SAME**, rc 0.

**Addendum (`evidence/031/`).**
- `archive.sha256` (`6f77e3c4…5484`) equals `git archive --format=tar.gz 48fcce0 tools firmware fpga/kv260/host`.
- The board file is a new run, not 029's (they differ from byte 292):
  - KV260, RTL `d1aa46f7ed7e`, `f_meas_mhz 49.99967`;
  - every result `pass: true`;
  - pulses `[[5,5]]`, `[[4,5]]`, `[[4,5]]`.
- `--compare` against the orchestrator's model file: arp phase 1, ping phase 2, udp phase 2, **SAME**.
- Against my own fresh model file: **SAME** too. The two model files are equal in every deterministic field and in `tx_phases`.
- What it shows at the board level: the new build path (`board_parts`, `board.reset`, `Hw(board=…)`) builds a working rig on the KV260, and the link pulse is now compared. It doesn't exercise any fault path; those have run on the fake board only.
- Evidence level: RTL in FPGA fabric (KV260), one 64-byte frame at a time, no cable. The selftest was not repeated.

## Notes

1. `test_a_rig_stopped_while_it_is_built_gives_the_board_back` injects its faults only at HwRig's 2 s clock measurement. The window from `select(fabric=True)` to that point (the ID read, the quad enable) goes through the same `except BaseException`, so I don't count it.
2. In `give_back`, `spim.mem.rd(pe_spim.SEL)` is outside `step()`. If that read raised, the later steps (pe_lab, PMOD, select) would be skipped. Wrapping it the same way would keep "every step is tried" true.
3. My 029 report, and item 029, put the TD sampler on core 1. It runs on core 0 (`ethhost.RX_CORE`); core 1 sends. `evidence/fabric-ping-20261103/README.md` on `docs-fix` repeats this (report 030, note 3).

## What I ran

**On runner1:**
- `remote_test.sh model` at 48fcce0: `model rc=0`, `2370 passed, 28 skipped in 619.28s (0:10:19)`, "SIGROK CHECKS SKIPPED: 28 tests" (checkout `agent-runs/protoemu-verify-48fcce0-925364`).
- In my clone `~/verifier-031/`, `python test/mutants.py 'hw-*' 'fab-*'`: `99 of 99 mutants killed`, rc 0.
- `eth_tap.py --backend fpga-model --probe --save tmp/m.json`: probe PASS.
- `--compare` of the 029 and 031 board files and of my two edited copies against it: results as above.
- `window2.py`: the six fault cases above.
- A timing of `FabricEth(...)` on the fake board: 3.3 ms.

**Locally:**
- `--compare` of `evidence/031/` b against m;
- the archive hash;
- the model-file field comparison.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
