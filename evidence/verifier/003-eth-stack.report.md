VERDICT: FAIL

Commit e71ab1c, base 61af9b2. The stack is solid: everything I ran on the model, RTL and gate level passes, and the gate-level monitor change hid nothing. It fails on claim 4, because the 16 us and 24 us software budgets were measured on 300-byte frames and do not hold on long ones, and on claim 1, because the 15/15 mutant result cannot be reproduced from the repo.

| Claim | Result |
|---|---|
| 1. Runs on the model and MicroPython's Unix port; 15/15 mutants | Stack PASS, no skips. 15/15 is true but not reproducible from the repo (finding 2) |
| 2. Carrier sense leaves 130 ns unheard against 3910 ns; no collision handling | PASS for 130 ns and the limit. 3910 is a coarse-grid figure, the fine sweep gives 3930 (finding 5) |
| 3. Link pulses 16.000 ms; eth10_tx 3.3 us after a frame; eth10_tx_cs restarts the timer | PASS |
| 4. SCK limits; budget 16 us RX, 24 us TX | SCK limits PASS. Budget FAIL (finding 1) |
| 5. RTL 7/7; gate level 3/3, docs only after; TAP 5/5 at both clocks | PASS. RTL and gate level rerun; TAP logs read, not rerun |
| 6. Gate-level monitor change hid no real fault | PASS at functional gate level (see below) |
| 7. Docs state nothing ran on FPGA, silicon or a cable | PASS |

## Claim 6: the monitor change

Decision: it did not hide a real fault at this evidence level. I tested it directly with a probe that reads TD right after each `uo_out` change and again at the end of that timestep.

| Run | Both-high right after a change | Still both-high at end of timestep | Mid-clock monitor |
|---|---|---|---|
| RTL, clean, 7 tests | 0 | 0 | Passes |
| Netlist, clean, 40 MHz ping | 1200 | **0** | Passes |
| Netlist, clean, 50 MHz session | 13164 | **0** | Passes |
| Netlist, clean, carrier sense | 1130 | **0** | Passes |
| Netlist, one injected both-high tick, 40 MHz ping | 1201 | **1** | **Fails, as it should** |
| Netlist, same fault, carrier sense | 468 | **1** | **Fails, as it should** |
| RTL, same fault | 1, 2, then 3 across the failing tests | same | **3 of 7 tests fail, as they should** |

- All 15,494 clean-netlist transients begin and end inside one timestep, so they are delta ordering in a zero-delay netlist, not a level the pads hold.
- The fault was one tick of `side 3` in TP_IDL, the 40 MHz clock's shortest case, placed where it breaks no frame decode. Only the monitor could see it, and it did.
- Limit of this decision: it covers functional simulation only. SDF was not run, as the doc says. The transient is probably real on silicon at a smaller scale, see finding 3.

## Findings

### Blocking

1. `docs/eth10-network.md:119-125`: the software budget is false for long frames.
   - `test_rx_service_latency` and `test_tx_service_latency` (`test/model/test_eth_stack.py:547-562`) run `SHORT`, 300 bytes (`:39`). The table does not say so, and the paragraph above it is about 1518-byte frames.
   - I searched the longest passing software time per pass, in 0.5 us steps, quad at SCK = clk:

   | Case | 300 bytes | 600 bytes | 1518 bytes | Doc |
   |---|---|---|---|---|
   | RX, 50 MHz | 16.5 us | 16.5 us | 16.5 us | 16 us, holds |
   | RX, 40 MHz | 16.0 us | 15.5 us | **15.5 us** | 16 us, false |
   | TX, 50 MHz | 25.5 us | 23.5 us | **22.5 us** | 24 us, false |
   | TX, 40 MHz | 24.0 us | 22.5 us | **21.5 us** | 24 us, false |

   - At the doc's own figures, RX at 40 MHz overflows at 600 bytes and up, and TX underruns at 600 bytes and up on both clocks.
   - `:152`, "about 16 us or less", is the go/no-go number for the KV260 bridge, so it should use the worst case.
   - Fix: run the budget tests at 1518 bytes and state the frame size. Worst cases: RX 15.5 us, TX 21.5 us.

2. `claim 1`: 15/15 is not reproducible from the repo.
   - The script exists only on runner1, at `agent-runs/eth-mutants.sh`, with a hard-coded path. Rerunning it at e71ab1c did give 15/15, none with a missing pattern.
   - No doc or commit message quotes the figure, so nothing published rests on it.
   - The repo already has `test/mutants.py`, a runner that requires a passing baseline. Fix: add these mutants there, or stop quoting 15/15.

### Should fix

3. `docs/eth10-network.md:45-46`, `test/test_eth_stack_rtl.py:56-57`: the direction is wrong. Both say an H to L flip passes through both-high.
   - The 9 samples I inspected (the first 3 in each clean netlist test) all go 3 to 1, ending at H, so those are L to H flips. I did not classify all 15,494.
   - That direction matches the physical skew. From my fo6 STA run for item 002, `uo_out[0]` arrives before `uo_out[1]`: 1.57-1.90 ns against 2.04-2.49 ns at the fast corner, and 3.63-4.35 ns against 4.77-5.85 ns at the slow one. So on L to H, TD+ rises before TD- falls.
   - These are STA arrivals at the macro's edge, not a measured overlap. But a both-high of very roughly a nanosecond on each L to H flip is likely on silicon. The doc's "a question for SDF or a scope" stands, and it can now say which edge.

4. `fpga/kv260/host/eth_tap.py:228,339-345`: `--measure-spim` changes board state, and its docstring says "touches no pin".
   - It calls `spim.select(fabric=True)` and `enable_quad()` and restores neither (`fpga/kv260/host/pe_spim.py:59-63,114-118`). The KV260 README records leaving the host port on the GPIO path.
   - Only the orchestrator touches the Kria, so it needs to know before running this. Fix: restore both, or say so.

5. `docs/eth10-network.md:72`: 3910 ns comes from a 500 ns grid. At 10 ns steps, 3930 ns is unheard and 3950 ns is heard. The doc does state the grid, and the comparison stands. `test/model/test_eth_stack.py:719` only asserts `blind >= 2000`.

6. `docs/eth10-network.md:81`: "at least 96 bit times" is not pinned.
   - A 95-bit window passes all 108 model tests, because `test/model/test_eth_stack.py:735` allows `IFG_NS - 100`. A 90-bit window is caught.
   - `test/test_eth_stack_rtl.py:265`, the RTL and gate-level case, asserts only `>= 9000` ns.
   - The firmware's 96 x 4 ticks is right, so this is a test gap, not a defect.

### Minor

7. `docs/eth10-network.md:216-218`: the TAP figures match the logs, but the doc does not say where the logs are. They are only at runner1 `agent-runs/eth-tap-50.log` and `eth-tap-40.log`. RULES.md wants a log behind a number, so cite the path.

8. My mutants found two untested checks in `tools/pe/eth.py`: `:308`, source 0.0.0.0 or broadcast, and `:148`, `total > len(p)`. Removing either passes all 108 tests. The second is nearly equivalent, since a later checksum usually catches it. `:34` of the doc lists 20 kinds of bad frame, and these are not among those proved.

## The TAP code

I did not run it or use sudo. Judged from the code, it is safe and its refusal logic is sound.

- `require_dedicated_netns` runs before any network action and has no off switch. It fails closed: a stat error, the host's namespace, or any interface but lo all refuse.
- Unusual setups fail in the safe direction. Under `--pid --mount-proc`, `/proc/1` is this process, so it refuses.
- `ip` commands, `ip neigh` and the `/proc/sys/net` writes all act on this namespace only. A bad `--tap` name fails at TUNSETIFF, before the sysctl path is built from it.
- `FpgaBackend` is a stub that exits. `--measure-spim` runs before the check, but opens no network; finding 4 is its only problem.
- `selftest` is honest: the "no reply" checks could pass on a dead bridge, but `answers_again` runs after them and is required.

## What held

- **Claim 1:** `test_eth_stack.py` shows 108 passed and no skips with `-rs`, so scapy, tcpdump and real MicroPython all ran. `pe.mpy_bundle --check` passes.
- **Claim 2:** 130 ns unheard and 150 ns heard, at 5 ns steps. The collision test passes.
- **Claim 3:** printed 16.000 ms and 100 ns for both firmwares, 3.3 us for `eth10_tx`, R1 = 9998 for `eth10_tx_cs`.
- **Claim 4, SCK limits:** pinned exactly in `RATES`. At 50 MHz, quad div 7 sustains RX and deferral, and div 6 sustains a collision, for zero, 0xFF, 0x55 and random payloads. 1-bit div 2 also holds on those I tried. The RTL edges pass.
- **Claim 5:** `e71ab1c` against `1999a39` touches only `README.md`, `docs/eth10-network.md` and `docs/guide.md`. The TAP logs show 5/5 and 2/2 at both clocks, `pass: true`, and match the doc's RTT table. Netlist hash `a94a948b` matches the receipt.
- **Claim 7:** `docs/eth10-network.md:7`, the README row and `docs/guide.md` all state the limits. Evidence levels are labelled per row, and the one memory-sourced 802.3 figure says so. No first or only language.
- **Mutations:** every core stack and firmware check tried was caught, see below.
- **Rules:** nothing under `src/` or existing `flow/` files changed. KV260 untouched.

## What I ran

All on runner1. Throwaway copies were removed afterwards.

- `remote_test.sh model test_eth_stack_rtl` at e71ab1c: `1503 passed, 23 skipped in 321.22s`, `test_eth_stack_rtl rc=0`, `TESTS=7 PASS=7 FAIL=0 SKIP=0`.
- `test_eth_stack.py -rs`: `108 passed`, no skip lines.
- `flow/gl_module_test.py` on fo6 (read only) at e71ab1c: `gl_module_test: 3/3 passed`; 1765, 180 and 225 s. My receipt shows `validation_dirty` true because my log and out directory sat in the clone; no tracked file changed. The builder's own receipt at 1999a39 is clean.
- Probe on the clean netlist: 3/3 passed. Fault runs: 40 MHz ping and carrier sense both failed on `TD+ and TD- both high`.
- Probe on RTL: clean 7/7; fault 4 pass, 3 fail.
- Builder's 15 mutants: all 15 stopped on test failures.
- My 20 mutants: 17 caught, 3 survived (0.0.0.0 or broadcast source, total length check, 95-bit window).

## Mistakes of mine, none affecting results

- The builder's script runs `git clean`, which deleted my first output file. I stopped it and reran with output outside the clone.
- A `pkill -f` matched my own ssh session. I confirmed the other jobs survived before continuing.
- My first `pe.mpy_bundle --check` lacked `PYTHONPATH`, and the rerun passed.
