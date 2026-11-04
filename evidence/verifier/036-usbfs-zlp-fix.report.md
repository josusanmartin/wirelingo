VERDICT: FAIL

Commit f88fb31 on base 97e442d (an ancestor). It follows 6dc8c93 (report 032) with five commits (cd7f33e, 64b59c2, fd650ee, 9d7c44b, f88fb31). The change is:
- `tools/pe/usbdev.py`, its MicroPython copy, and a docstring in `usbfsdata.py`;
- the re-stage test and seven mutants;
- `fpga/kv260/README.md`;
- 19 evidence files.

Nothing in `firmware/`, `src/` or `flow/` changed.

**What holds:**
- The re-stage fix is sound where it matters: in no case I could construct does stale DATA reach the wire. That includes four hold points of the builder's and two of mine.
- The "never staged" argument holds against the firmware's SETUP flush and the ISA's FIFO rules (Checked, below).
- The tests fail on the 032 code and the 10 mutants are killed.
- The evidence is byte-identical to the orchestrator's files, with every sha256 right.
- The suite and the model demos pass.

**Why it fails:**
1. The open window the README describes is narrower than the real one. The software check is two reads, and a SETUP that lands between them, while its DATA is still on the bus, also leaves F_STAGED set over a flushed FIFO. The test's allowance table leaves that case out.
2. The README's new enumeration-sweep figure ("again with the re-stage checks of 64b59c2") has no script or log anywhere I can find, nor do the 160/160 and 6-of-40 figures around it.

| Claim | Result |
|---|---|
| 1. Re-checks before and after FSET; the dirty TX FIFO emptied before the next stage; MicroPython copy identical; `mpy_bundle --check` | PASS. The window statement is not right (finding 1) |
| 2. Test holds at four points, two resume modes; 6dc8c93 fails the first-write cases with 032's message; device-state checks | PASS |
| 3. Remaining window: "the single SPI access between the last check and FSET"; 8 unanswered INs in the model; allowed only in those two cases | 8 and 1 reproduced. **The window is wider** (finding 1) |
| 4. Seven new mutants; `usbfs-*` `usbdev-*` 10/10 | PASS |
| 5. Evidence verbatim from `evidence/032/`, sha256s, level, the 6dc8c93 runs in the README, 300/300 and HC-death runs kept with logs | PASS |
| 6. Model suite 2314 passed, 28 skipped; sweep 160/160; replay ls-dev/fs-dev identical | Suite and replay PASS. **Sweep: no log** (finding 2) |

## Blocking

1. **The remaining window includes the RX-level read of the pre-FSET check, not only the access after it.**
   - **The code.** `stage()` (`tools/pe/usbdev.py:142-153`) checks `setup_since(mh.read(sim.A_FSET))`: first the flags read, then a separate read of the RX core's STATUS (`:123-128`).
   - **The failure.** A SETUP token can arrive after the flags read. The firmware then clears F_STAGED and empties the TX FIFO, and only sets F_SETUP after that. If the SETUP's DATA is not yet in the RX FIFO when the STATUS read happens, both reads pass. FSET then sets F_STAGED over a flushed FIFO, exactly the state the README puts only in "the one SPI access between the last check and the FSET".
   - **How I showed it.** `test_v036_holds.py` runs the builder's `test_no_restage_after_a_setup` unchanged, with two more hold points. Each is a hold on the STATUS read whose access before it was the flags read: one where the access before that was a TX FIFO push (inside the pre-FSET check), and one where it was the FSET write (inside the post-FSET check). On a `git archive` copy of f88fb31:
     ```
     FAILED test_v036_hold[pre-FSET status read-after its token]
     AssertionError: SET_CONFIGURATION: status stage saw [('IN', None, None), ('IN', None, None), ('IN', 75, '')]
     1 failed, 3 passed
     ```
     That is 2 unanswered INs, then the right zero-length DATA1. The case is outside `UNANSWERED` (`test_usb_fs_data.py:283`), so the test as written would fail it.
     - With the whole SETUP in ("after the SETUP"), the RX level is already up, and the check catches it.
     - The post-FSET point passes both ways: there the firmware's own `flag clr F_STAGED` comes after the FSET.
   - **What doesn't change:** no stale DATA was sent. The claim "never stale DATA" holds.
   - **Unanswered INs** with a counter on `status_in`:

     | Point | Unanswered INs | Who reported it |
     |---|---|---|
     | `at FSET`, after the SETUP | 8 | the builder; matches the README |
     | `after FSET`, after the SETUP | 1 (allowed 2) | the builder |
     | between the check's two reads, after its token | 2 | mine |
     | every other point | 0 | |

   - **Fix.**
     - Say the window runs from the flags read to the FSET (the RX-level read and the FSET write), or "the end of the check".
     - Add this hold to `HOLDS` with its own allowance.
     - The orchestrator's firmware-guard decision should be made on the wider window.

2. **The enumeration-sweep figures have no script or log.**
   - **What the README says** (`fpga/kv260/README.md:1430-1441`): "all 160 enumerations with the software before the fixes above (c184dd6), and all 160 with them, at 8c71177 and again with the re-stage checks of 64b59c2. (At 200 us per transaction, 6 of 40 failed earlier, at SET_ADDRESS …)".
   - **What's new in f88fb31:** "again with the re-stage checks of 64b59c2". Its commit message gives the parameters (20-100 µs per SPI transaction, 0.2-2 ms pauses, 0 re-stages) but no path.
   - **Where I looked:**
     - no sweep script or test in the tree (`test_usb_fs_data.py`'s `GAPS` sweep is a different, smaller test);
     - nothing in the item;
     - on runner1, the builder's run directories (`agent-runs/agent-a158fcfcc32d11458-*`) hold only `remote_test.sh` output.
   - **The rule:** RULES says a number in a doc needs a test or log behind it. This is the same as 032's finding 2, for a model number.
   - **My own miss:** the c184dd6 and 8c71177 parts were already in 6dc8c93's README, and my 032 report checked only that they were labelled "evidence model only". I should have asked for the log then.
   - **Fix:** commit the sweep script (or put it under `test/`) and its output for the three commits, or cite the log's path.

## Checked

**The "never staged" argument, against the firmware and the ISA.** The flush is firmware, running on the pe core: `got_setup` in `firmware/usb_fs_dev.pasm:160-171`. It does `flag clr F_GOTACK`, then `flag clr F_STAGED`, then loops `jmp txne, drop` / `pull block` until TXF is empty, and only then `flag set F_SETUP`, `flag set F_EXP`. Against that:
- **A SETUP during the pushes.** Its flush drops the words already in, and later pushes land behind it. F_SETUP is set as soon as the loop ends. It stays set until the DATA's trailer, by which time autopush (24 bits) has put the DATA's words in the RX FIFO, so the pre-FSET check sees one or the other. Not staged, `dirty = 1`.
- **A SETUP after the flags read.** Every push had completed before that read (SPI accesses are serial), so the loop drops them all. The ISA says FIFO levels are taken at the start of the cycle and a core can't pull a word pushed in the same cycle (`docs/isa.md:621-623`). No pushes are in flight then, so nothing is left behind. What is left is F_STAGED over an empty TXF: finding 1's window, not stale DATA.
- **A SETUP after the FSET.** The firmware's own `flag clr F_STAGED` and flush cover it.

This is from reading the firmware and the ISA, plus the model runs. I ran no RTL simulation of the race.

**Claim 1.**
- The changed lines of `tools/pe/usbdev.py` and `tools/micropython/usb_device.py` are the same text, leading whitespace aside.
- `python -m pe.mpy_bundle --check`: "firmware bundle matches validated sources".
- The dirty clear keeps CTRL's enable bits (`& 15`), clears the TX core's FIFOs (`1 << 16+tx`), and restarts it only for `dirty == 2`. Restart keeps the FIFOs and resets PC, ISR and OSR (`isa.md:127-142`).

**Claim 2.** `f88fb31`'s `test_usb_fs_data.py` on earlier code:

| Code | Result |
|---|---|
| 6dc8c93 | 6 failed, 19 passed. The two "first write" cases fail with `SET_CONFIGURATION: status stage saw [('IN', 75, 'b0b1b2b3b4b5b6b7')]`, 032's message. The two "at FSET" cases fail with "the software never got at FSET" (that code has no pre-FSET read). The two "decision" cases fail only on `sw.tx_clears`, an attribute that is new. |
| cd7f33e (tests only) | the same 6 failed |
| 64b59c2 | 25 passed |
| f88fb31 | 25 passed |

**Claim 4.** `test/mutants.py "usbfs-*" "usbdev-*"` in my clone: `10 of 10 mutants killed`. The new seven are killed by the hold point each names (`usbdev-restage-unchecked` by `at the decision`; `fset-unchecked-before` and `dirty-not-cleared` by `at the first write`; `fset-unchecked-after` and `dirty-no-restart` by `at FSET`).

**Claim 5.**
- **The tarball:** `evidence/032/fstall-fix-20261103.tar.gz` has sha256 `f88fe59048e2…c9400d`, matching the README. I extracted it into its own scratch folder.
- **Byte for byte** (from `git cat-file blob`), each committed file is identical to its source:
  - `fix200.log`, `fixnodata.log`, `fixread10.log`, `fixread200.log`, `quirkm200.log` and `nodata2.py` against the tarball;
  - `stalls/fix-demo-{19,20,21,80,133,161}.log` against `runs-fix-20261103T212020Z/demo-N.log`, and `stalls/quirkm-demo-{40,146}.log` against `runs-quirkm-20261103T212944Z/`;
  - `nodata1000.log`, `kernel-19h05-19h25.txt`, `nodata300.log`, `nodata.py` and `nodata2.py` against `evidence/032/nodata/`.
- **The README's sha256 list:** all 18 entries match the committed blobs.
- **The logs say what the README says:**
  - `DONE runs=200 fails=6` and `fails=2`;
  - "NODATA 1000 sent: 1000 ok … median 0.85 max 1.87";
  - 10 ok, median 0.81; 200 ok, median 0.74, max 1.46;
  - 300 ok, median 0.74, max 1.90;
  - `no-data 1: errno 32`, `no-data 3: TIMEOUT`, then errno 19;
  - "HC died" at 19:10:27.
- **Level:** RTL in FPGA fabric, protoemu48 `60b0692`, xHCI and USB5744, "Not silicon".
- **Privacy:** no email, MAC, tailnet name, `[home directory redacted]` or build-machine name. Only the board's hostname `kria` and `/home/ubuntu/fstall` appear.

**Docs.**
- `pe.usbfsdata`'s docstring now cites the board runs.
- The README keeps the stall's cause as "likely explanation, but it is not proved".
- "Narrowed, not closed" replaces "fixed".
- The firmware guard is described and "not applied".

**Claim 6.**
- **Suite:** 2314 passed, 28 skipped.
- **Model demos:** `usb_demo.py --model --speed low` and `--speed full`: PASS (model).
- **Replay:** `usb_replay_demo.py --model --plans … ls-dev fs-dev` at 6dc8c93 and at f88fb31: ALL PASS both. I compared the two `--save` files field by field:
  - every packet's content is the same;
  - the one difference is segment 22 of each capture, every edge 8 ticks later (packets 62-64 start 8 ticks later). That is consistent with the extra reads in `stage()`.

  The claim "identical requests, re-stage count and wire packets" holds for content.

## Notes

1. **What 8 unanswered INs mean on a real host.** Linux's xHCI driver gives a control endpoint an error count of 3 (xHCI spec, `CErr`). So the third unanswered IN in a row ends the control transfer with an error before the device's next `service()`. The README's own `nodata1000.log` run shows a cancel after unanswered INs taking the HC down. This is from the spec and the driver, not tested here; the narrowed paragraph should say it.
2. **Restart happens whether or not the core parked.** `dirty = 2` restarts the core even when the SETUP came after the FSET, where the firmware cleared F_STAGED itself and the core never parked. That is the `after FSET` case's one cut IN, which the README mentions. Checking STATUS `[12]` (stalled) before restarting would avoid it.
3. **Record-loss window, by reading only.** On the one-core device, the dirty clear reads the RX FIFO (`pop_all`, two accesses) and then clears both FIFOs with a CTRL write. A record pushed between them, such as a SETUP that aborts the transfer and that the firmware has already ACKed, would be lost. It needs a dirty FIFO and a host abort within about one SPI access, and I did not construct it.

## What I ran

**On runner1:**
- `remote_test.sh model` at f88fb31: `model rc=0`, `2314 passed, 28 skipped in 674.44s (0:11:14)` (checkout `agent-runs/protoemu-verify-f88fb31-1401947`, removed).
- In `~/verifier-036/`:
  - `test_v036_holds.py` on an f88fb31 copy: `1 failed, 3 passed`;
  - the same with a counting wrapper on `status_in`: the counts above;
  - f88fb31's `test_usb_fs_data.py` on 6dc8c93, cd7f33e and 64b59c2 copies, with results as above;
  - `test/mutants.py "usbfs-*" "usbdev-*"` in a clone: `10 of 10 mutants killed`;
  - `mpy_bundle --check`, `usb_demo.py --model` at both speeds, and `usb_replay_demo.py` plans and `--model` at 6dc8c93 and f88fb31, with the field comparison. My runs without `--model` were refused by fabric_guard, which is right without xmutil.

**Locally, read only:**
- the tarball, hashed and extracted;
- the byte comparisons and the sha256 list;
- the logs;
- the firmware and the ISA.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
