VERDICT: FAIL

Commit 6dc8c93 on base 97e442d (an ancestor), five commits. The change is host software, tests and `fpga/kv260/README.md`; nothing in `firmware/`, `src/` or `flow/` changed.

**What holds:**
- The three fixes are right, minimal, and right for low speed too.
- Each new test fails on the software before its fix and passes after, at the commits the item names.
- The five mutants are killed, and the model suite and demos pass.
- The board logs reproduce the item's numbers.
- The docs keep the stall open and its cause unproved.

**Why it fails:**
1. The re-stage fix narrows the race but does not close it. A SETUP that lands between the new checks and `stage()` still puts the old DATA in front of SET_CONFIGURATION's status reply. The doc calls it fixed.
2. A board number in the doc has no log behind it, and the doc doesn't yet carry the 6dc8c93 board runs.

| Claim | Result |
|---|---|
| 1. usbfsdata: zero-length write staged twice, zero-length read none; test fails at e61c961, passes at c184dd6 | PASS: reproduced, same failure text |
| 2. usbdev GET_DESCRIPTOR: no ZLP at exactly wLength; low speed passes | PASS |
| 3. usbdev re-stage waits if a SETUP has arrived; each half has a mutant | Literally true. **The race remains** (finding 1) |
| 4. f2e7d62: 4 failed; 6dc8c93: 2308 passed, 28 skipped; 5/5 mutants; `mpy_bundle --check`; model demos | PASS |
| 5. Model cannot reproduce the stall; README says so | PASS |
| Board runs (orchestrator) | Reproduced from `fstall-fix-20261103.tar.gz` (note 1 on the reads) |
| Docs: stall not fixed, board evidence at its level, cause not claimed | Cause not claimed: PASS. **Evidence: finding 2** |

## Blocking

1. **The lost-ACK re-stage can still go in behind a SETUP's flush.** The check `not flags & F_SETUP and not <RX FIFO level>` (`tools/pe/usbdev.py:173-174`, and the same in `tools/micropython/usb_device.py`) is made from two reads. `stage()` then writes FCLR, the TX FIFO words and FSET (`:120-127`). A SETUP that arrives after those reads and before the writes is flushed out first, and the stale packet is staged after it. That is the failure the fix is for.
   - **The test doesn't cover it.** `test_no_restage_after_a_setup` deschedules the software at the FSET read just after `pop_all`, which is before the check.
   - **How I showed it.** I wrote `test_v032_race.py`: the same scenario, but held one step later. The hold is on the first write (`A_FCLR`) when the previous access was the RX core's STATUS read, so both checks have already passed. In a `git archive` copy of 6dc8c93 it fails in both resume modes:
     ```
     AssertionError: SET_CONFIGURATION: status stage saw [('IN', 75, 'b0b1b2b3b4b5b6b7')]
     ```
     That is the old DATA1 answering SET_CONFIGURATION's status IN, the same message as the test at f2e7d62.
   - **The doc overstates.** `fpga/kv260/README.md` ("Two more `pe.usbdev` races … fixed 2026-11-03") says the re-stage race is fixed. It is narrowed: from "any time in the 100 µs decision" to the few SPI accesses between the check and the first stage write.
   - This race needs a lost ACK, and the board stalls show 0 re-stages, so it is not the stall.
   - **Fix:** after staging, re-check F_SETUP and the RX level, and if a SETUP came, clear the TX FIFO again. Or say "narrowed" and name the window.

2. **A board figure in the doc has no log, and the 6dc8c93 board runs are not in the doc.**
   - **The missing log.** `fpga/kv260/README.md` says: "with the write fix patched into the software at run time, 300 of 300 zero-length writes on a configured device passed (median 0.74 ms, max 1.90 ms, orchestrator run, 2026-11-03)". No log of that run is in:
     - `evidence/032/` (its runs are the attach hunts and the `fstall-fix` tarball);
     - `fstall-fix-20261103.tar.gz` (its write run is 1000 sent, median 0.85, max 1.87);
     - the orchestrator's earlier `fstall-20261103.tar.gz` (sha256 `d81348f9…`).

     Nor is the HC-death run's log; its kernel lines appear only in `notes/F-usb-stall-20261103.md`. RULES: a number in a doc needs a log behind it.
   - **The doc predates the board runs.** The commit (21:05 UTC) is older than the runs of 21:20-21:50. So the doc says "Zero-length reads have not been run on the board", and says nothing of the 200 attaches with the fixed software: 6 stalls, the same rate as before.
   - **Fix:** before merge, replace the 300-run sentence with the 6dc8c93 runs and their logs, at their level (RTL in FPGA fabric, protoemu48 RTL 60b0692, one host, one hub). That means the 1000 writes, the 10 + 200 reads, 6/200 stalls with the fixed software, and 2/200 with the usbcore `m` quirk. Commit or cite the logs.

## Checked

**Fix 1 (`usbfsdata`).**
- **Writes.** `handle()` no longer sets `pending = [b""]` for a zero-length REQ_WRITE. `service()` stages the status ZLP once, when `len(incoming) >= expected` (0). Before, `handle` staged one and `service` set another after the first was sent.
- **Reads.** A zero-length REQ_READ now gets one ZLP: `… or not wlen`, with `and` binding first, so short transfers are unchanged. With no data stage the status stage is an IN, so that is right.
- At e61c961 (tests only) `test_usb_fs_data.py` gives 2 failed (`test_zero_length_writes_with_software_running_concurrently`, `test_round_trips_with_software_running_concurrently[0]`), first `zero-length write 11: status stage saw [('IN', 195, '')]`. At c184dd6: 15 passed.

**Fix 2 (`usbdev` GET_DESCRIPTOR).**
- The ZLP now goes only when the data is a multiple of 8 and shorter than wLength, or when wLength is 0. That is USB 2.0 §5.5.3 for an 8-byte EP0, the only size this device uses at either speed.
- An empty descriptor with wLength > 0 still gets its ZLP. `usbdev` and the MicroPython copy changed identically, and `mpy_bundle --check` passes.

**Fix 3 (`usbdev` re-stage).**
- STATUS bits `[7:4]` are the RX FIFO level (`isa.md:760`), read on `self.rx`. That is core 0 in the two-core low-speed device and the one-core devices, so it is right at low speed too.
- An RX FIFO holding any record, not only a SETUP, also defers the re-stage until the next `service()` drains it. That is safe.

**Item claim 4, f2e7d62 (tests only).** `test_usb_fs_data.py` gives 4 failed: `test_no_restage_after_a_setup[after its token]`, `[after the SETUP]`, `test_descriptor_ending_at_wlength_has_no_zlp[8]`, `[16]`.

**Mutants.** `python test/mutants.py` with the five names: `5 of 5 mutants killed`:
- `usbfs-zlp-staged-twice` by `test_zero_length_writes…`;
- `usbfs-read0-no-status` by `round_trips…[0]`;
- `usbdev-zlp-at-wlength` by `descriptor_ending…[8]`;
- `usbdev-restage-in-setup` by `no_restage…[after its token]`;
- `usbdev-restage-after-setup` by `no_restage…[after the SETUP]`.

**Demos (model):**
- `python -m pe.mpy_bundle --check`: "firmware bundle matches validated sources";
- `usb_demo.py --model --speed low` and `--speed full`: PASS (model);
- `bringup_kria.py --model`: ALL PASS;
- `usb_replay_demo.py --model --plans … ls-dev fs-dev`: ALL PASS. My run without `--model` was refused by fabric_guard, which is right on a machine without xmutil.

**Board evidence**, from `fstall-fix-20261103.tar.gz`, extracted into its own scratch folder:
- **200 attaches** (`runs-fix-20261103T212020Z`): 194 PASS, 6 not. Each of the 6 has requests `…0900`, `…1200`, `0009010000000000`, `F_SET 0x10`, "core 0 TX FIFO 2 words, RX FIFO 0, PC 4", "software staged (11, b'')", "0 packets re-staged". `fix200.log`: `DONE runs=200 fails=6`.
- **Quirk `m`** (`runs-quirkm-…`): 198 / 2, the same signature. `quirkm200.log`: `fails=2`.
- **Zero-length writes.** `fixnodata.log`: "NODATA 1000 sent: 1000 ok, 0 timed out … median 0.85 max 1.87".
- **The read logs.** `fixread10.log`: 10 ok, median 0.81. `fixread200.log`: 200 ok, median 0.74, max 1.46.
- Every log reports RTL `60b06926cb1d` at 47.998 MHz.

**The docs on the stall.**
- "**Open: `SET_CONFIGURATION` stall** … The cause is not proved: nothing here sees the bus between the xHCI controller, the hub's transaction translator and the device".
- The model section ends "The device software is not ruled out by this, but it has no known path to the stall".
- Nothing claims the stall is fixed or its cause proved.
- The 160/160 model claim is labelled "evidence model only".

## Notes

1. **The read logs don't show they were reads.** `nodata2.py` takes the request type from `NODATA_RT` (default `0x40`, a write), and its log line doesn't print it. Only the orchestrator's notes say `0xC0 0x11`. Have the script print RT and RQ, so the log carries the direction.
2. **The notes go further than the evidence.** The orchestrator's notes conclude "The trigger is on the host side (ZynqMP xHCI and the USB5744's TT)". The evidence fits that: the status ZLP staged, the core idle, no IN reaching it. But no capture of D+/D- shows whether IN tokens reach the pins, and the notes' own "Next (decisive)" says so. If this goes into the docs, it is a likely explanation, not a proved cause.
3. **The `usb_fs_data_demo.py` stall printout is reads only.** `C_STATUS` and `C_PC` of core 0, and the software's own fields. It is what produced the board signature above.

## What I ran

**On runner1:**
- `remote_test.sh model` at 6dc8c93: `model rc=0`, `2308 passed, 28 skipped in 343.65s (0:05:43)` (checkout `agent-runs/protoemu-verify-6dc8c93-942116`).
- In my clone `~/verifier-032/`:
  - `test_usb_fs_data.py` in `git archive` copies at e61c961, f2e7d62 and c184dd6, with results as above;
  - the five mutants;
  - `mpy_bundle --check` and the four model demos;
  - `test_v032_race.py` (finding 1), 2 failed.

**Locally, read only:**
- the evidence tarball, extracted;
- the run logs;
- the orchestrator's notes;
- the earlier `fstall-20261103.tar.gz`, listed and grepped.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
