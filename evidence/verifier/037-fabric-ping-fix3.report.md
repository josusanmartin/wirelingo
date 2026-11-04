VERDICT: PASS

Commit 1b3ff6b on base 97e442d (an ancestor). It follows c4ea377 (report 034) with b24511c (code, tests, mutants) and 1b3ff6b (docs). The change is:
- the new `fpga/kv260/host/board_hold.py`;
- `eth_tap.py` and `fabric_eth.py`;
- `test/model/test_fabric_ping.py` and `test/mutants.py`;
- `docs/eth10-network.md`.

Nothing in `src/`, `flow/` or `firmware/` changed.

034's window is closed by ownership, not by moving a try. `board_rig` hands `run_closing`'s holder a closer before its first write that changes the board, and `run_closing`'s finally runs whatever the holder has. I could not find a point between the holder and the backend where an end leaves the board on the fabric path, at line level or at opcode level.

| Claim | Result |
|---|---|
| 1. Holder set before the first state-changing write; closer is `HwRig.close` once the rig exists, `give_back` before; finally closes what the holder has; nothing catches in `board_rig` or `FpgaBackend` | PASS |
| 2. One flag for SIGINT, SIGTERM and SIGHUP; first ends the run, later ignored; `RunStopped` a BaseException; `SessionSignal` kept for `--measure-spim` | PASS (note 1 on the finally's first statement) |
| 3. Before the holder only `board_parts` has run: maps, reads VERSION and MAGIC, writes the idle levels | PASS, checked against `pe_kria`, `pe_spim` and `pe_lab` |
| 4. Exhaustive line-event test, 621 points, Ctrl-C and SIGTERM | PASS. I repeated it at every **opcode**: 4186 points, all given back |
| 5. Four new mutants; exhaustive test alone fails all 16 parts against the holder mutants; 103/103 | PASS |
| 6. Model suite 2400 passed, 28 skipped; model probes 50/40 MHz | PASS |
| Board smoke run (orchestrator) | PASS: archive is 1b3ff6b; `--compare` SAME against their model file and mine |

## Checked

**Claim 1 (`fabric_eth.py:388-404`, `eth_tap.py:650-691`).**
- **`board_rig`** checks the guard, builds the parts, makes `BoardCloser(board, spim, lab)` and calls `hold(closer)`. Only then does it call `board.reset()` and `closer.rig = pfd.HwRig(...)`. It raises `ValueError` without a holder.
- **`BoardCloser`** runs `rig.close()` when `rig` is set (falling back to `give_back` if that raises), and `give_back` otherwise.
- **`run_closing`'s finally** does `held.close()`, then `backend.close()` only when nothing was held (the model backends). `Held.close` takes the closer and clears it in one tuple assignment, then runs it under `shielded`.
- **The seam inside `closer.rig = pfd.HwRig(...)`**, after the call and before the store, gives the parts-only path. I checked that this is enough there:
  - `HwRig.__init__` (`postfreeze_demo.py:85-100`) and `Hw.__init__` (`v2_demo.py:62-78`) only move SEL to the fabric master, read the ID, turn quad on, read pe_lab's identity and measure the clock;
  - the cores are still stopped from `board.reset()`;
  - `give_back` undoes each of these (quad off while SEL is 1, pe_lab GPIO, uio, PMOD, SEL to GPIO).
- **Nothing on the run path can swallow `RunStopped` or KeyboardInterrupt.** `grep` for bare `except:`, `except BaseException` and `except KeyboardInterrupt` over `eth_tap`, `fabric_eth`, `board_hold`, `postfreeze_demo`, `postfreeze2_demo`, `usb_replay_demo`, `v2_demo`, `pe_kria`, `pe_spim`, `pe_lab`, `fabric_guard`, `tools/pe/{asm,eth,sim,host,ethhost,modelhost}.py` and `tools/micropython/protoemu.py` finds only `eth_tap.py:776`. That is the bridge loop's own Ctrl-C, which returns through the finally.

**Claim 3.**
- `board_parts` builds `Board()`, `Spim()` and `Lab()`, each mapping only after `fabric_guard.require_app()`.
- `Spim()` reads VERSION and `Lab()` reads MAGIC; neither writes.
- `Board()` writes one GPIO word (`pe_kria.py:82-91`): CS_n high, rst_n high, uio drive 0xFF, and `ui_in[4:0]` 0. The docs list the first three; the fourth is also the give-back value.
- So an end there leaves the board where the closer would put it.

**Claim 4: my opcode-level repeat.**
- **Why.** Line events can't land inside a line, such as between `pfd.HwRig(...)` returning and `closer.rig` being stored, or between `make(held)` returning and `backend` being stored.
- **How.** `test_v037_opcodes.py` uses the builder's harness (`fake_parts`, `board_end_state`, `TRACED`) through `eth_tap.main(["--backend","fpga","--probe"])`. It turns on `f_trace_opcodes` for every traced frame (on Python 3.12.3 that takes a re-arm of `sys.settrace` to take effect) and injects at every opcode event, from the call of `Board.reset` until `run_closing`'s `backend` is set.
- **Result.** 4186 points; a Ctrl-C and, separately, a real `os.kill(SIGTERM)` at each one, sharded 24 ways under `-n 12`: `48 passed`. Every run ended with KeyboardInterrupt or rc 143 and "giving the board back", and the end state was right: SEL ends 0, quad off, pe_lab GPIO, cores stopped, PMOD writes 0.
- **The harness can fail.** Against the builder's two holder mutants, applied in copies, it gives `48 failed` for `fab-held-after-the-rig` and `48 failed` for `fab-holder-not-closed`.

**Claim 5.**
- `test/mutants.py "hw-*" "fab-*"` in my clone at 1b3ff6b: `103 of 103 mutants killed`. The four new ones are killed by:
  - `fab-sigint-not-ours`: `…on_a_signal[2]`;
  - `fab-holder-not-closed` and `fab-held-after-the-rig`: `…built_gives_the_board_back[ctrl-c]`;
  - `fab-closer-gives-back-parts-only`: `test_the_closer_stops_the_cores_once_the_rig_exists`.
- The builder's `test_fabric_ping.py -k every_point` alone: `16 failed` against each of those two holder mutants.

**Claim 6.**
- My stock run: 2400 passed, 28 skipped.
- `eth_tap.py --backend fpga-model --probe` at 50 and 40 MHz: `probe PASS` both.
- 034's four cases are tests in the file and pass in the stock run.

**The board smoke run** (`evidence/037/`):
- **The archive is the commit.** The orchestrator's `fabric-ping.tgz` (sha256 `868365d1…1a89`, as `archive.sha256` says) is byte-identical to `git archive --format=tar.gz 1b3ff6b tools firmware fpga/kv260/host`. Its 150 files equal the tree.
- **The board file is new.** `fabric-ping-b.json` has sha256 `0a211e91…`; the 029 and 031 board files are `08b7d601…` and `f424b00b…`. KV260, RTL `d1aa46f7ed7e` (clean), 49.99922 MHz measured, all five probes pass.
- **The comparison.** I made my own model file at 1b3ff6b on runner1 (it differs from theirs only in the eight `rx_wall_ms`/`tx_wall_ms` fields). `--compare` gives SAME against both:
  - arp: link pulses `[[5, 5]]`, 947 edges, phase 4;
  - ping: `[[5, 5]]`, 939 edges, phase 4;
  - udp: `[[4, 5]]`, 917 edges, phase 2;
  - bad_fcs and other_mac: no reply on either side.

  That is the item's figures. No fault was injected on the board, so the board run says nothing about the close paths; the model and the fake board carry those.

## Notes

1. **The finally's first statement, at opcode level.** I also injected at every opcode of `run_closing`'s finally before `Held.close` is entered (`eth_tap.py:684-686`), after a normal end and after an exception in the body (`test_v037_finally.py`).
   - **What it shows.** A real SIGTERM handled at any of the three opcodes before `live[0] = False` has stored escapes as `RunStopped`, and the board is **not** given back (SEL writes `[1]`). From the store on, it is ignored and the board is given back.
   - **Why this isn't a finding.** As I read CPython 3.10 and 3.12, neither handles signals at those opcodes. 3.12 checks at `RESUME`, backward jumps and after C calls; 3.10 at jumps and calls. The next check is `Held.close`'s entry, where `live` is already False. My injection ran the handler there only because the trace function itself is a call. So I have not shown it on a real run.
   - **Suggested hardening.** It is cheap to make it independent of the interpreter: `signal.pthread_sigmask(SIG_BLOCK, SIGNALS)` before leaving the try, or `live[0] = False` at the end of the try and of each except as well. The Kria's Python version is not recorded in the repo or the evidence; KV260 Ubuntu 22.04 images ship 3.10.
2. **"Exhaustive over the code that runs in between"** (`docs/eth10-network.md`) is exhaustive over host-code line events, as the next sentence says. My opcode run backs the claim at a finer grain, and could replace or join it.

## What I ran

**On runner1:**
- `remote_test.sh model` at 1b3ff6b: `model rc=0`, `2400 passed, 28 skipped in 342.97s (0:05:42)` (checkout `agent-runs/protoemu-verify-1b3ff6b-1447683`, removed).
- In `~/verifier-037/`:
  - `test_v037_opcodes.py`: count `4186 opcode events`; `48 passed` at 1b3ff6b; `48 failed` on each of the two mutant copies;
  - `test_v037_finally.py`: the table in note 1 (`4 failed`, as described);
  - `test_fabric_ping.py -k every_point` on the two mutant copies: `16 failed` each;
  - `test/mutants.py "hw-*" "fab-*"`: `103 of 103 mutants killed`;
  - model probes at 50 and 40 MHz; my own model `--save`;
  - `--compare` of the board file against both model files.

**Locally:**
- the diff;
- the constructors and close paths, read;
- the archive hash and tree comparison;
- the board-file hashes.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
