VERDICT: FAIL

Commit 5554b88 on base bc98a97 (an ancestor). Two commits:
- **c4003a2:** firmware, software, tests and mutants;
- **5554b88:** the board script, figures, docs and evidence. Its firmware change against c4003a2 is a header comment only.

Nothing in `src/` or `flow/` changed.

The guard is right, and it closes the window: every claim about the firmware, the software, the tests and the figures reproduces. The FAIL is the asked label check. Pre-guard board and gate-level figures for `usb_fs_dev` and `usb_ls_dev` stand unlabelled in three docs the item does not list, and one count the branch made stale is left in a fourth.

| Claim | Result |
|---|---|
| 1. got_in = `jmp !f F_STAGED, nak_in` / `jmp txne, send_data`; 141→142 and 129→130 words; usb_ls_tx unchanged | PASS (assembled: 142 and 130); usb_ls_tx and usb_ls_rx untouched |
| 2. pe.timing: in_data_ta [12,17]→[13,18], in_nak_ta [10,15]→[10,16], the rest unchanged, all PASS; usb_ls_dev unannotated | PASS: my run at both commits equals the evidence files apart from line numbers; hand count against the ISA agrees |
| 3. Restart removed; every-access test in the suite, all 0; on bc98a97 fails at exactly 8, 9, 10; `test_usb_in_guard` both speeds | PASS |
| 4. Suite 2597/29 (2625/1 with sigrok-cli); RTL `test` 27/27; test_sigrok 8/8; lockstep 6/6; mutants 11/11; pio_comparison pins 130 | PASS |
| 5. Figures; every board, gate-level and SDF figure labelled "not re-measured" | Figures PASS. **Labels FAIL** (finding 1); a stale count (finding 2) |
| 6. `usb_guard_run.py` refuses unless root with protoemu48; proved with `--model` | PASS (read; `--model` run) |
| 7. Sweep prints the harness sha256; bundle regenerated, `--check` passes | PASS |
| 8. Two-core LS can still send stale DATA (by reading) | Confirmed by reading (below) |

## Findings

1. **Pre-guard board and gate-level figures without the label.** The item says every such figure is marked in the kv260 README, top README, `explain-wirelingo.md` and `physical.md`. The kv260 README, `explain-wirelingo.md:540-543`, `physical.md:683` and the top README's data-demo sentence (`README.md:69-71`) are marked. These are not:
   - **`README.md:209`, the `usb_ls_dev` row.** "KV260: enumeration against a replayed host, every pad edge equal to the model's." That replay ran the 129-word firmware. With the guard the model's pad timing moves (the evidence's own `compare-dev-replay.txt`: "pads equal False"), so for the current firmware that sentence is not true until the board replay is redone.
   - **`README.md:210`, the `usb_fs_dev` row.** "RTL and gate-level (signed-off netlist): SETUP/DATA0, ACK at 10.5 clocks …, NAK, staged DATA1 …; KV260 at 48 MHz against a replayed host, every pad edge equal to the model's." The gate-level run and the KV260 replay are pre-guard, and the row doesn't say so. The same `README.md` marks the data demo two sections up but not these rows.
   - **`docs/verification-story.md:28`.** "Data moves over full-speed USB | 350 of 350 round trips equal, over 2 MB, at 17.6 kB/s | RTL in FPGA fabric | … 5 of 116 attaches stall." Every one of these is a pre-guard board figure for `usb_fs_dev`, with no label. The neighbouring rows of that table do carry "before fault-fixes; needs a rerun" for `usb_enum`, which is the pattern to follow.
   - **`docs/verification.md:354-359`.** The gate-level SDF set "and the full-speed USB device … (test_usb_fs_device) at 48 MHz …, each at typ, slow and fast. Run fo6 passes all of it." That is `usb_fs_dev` before the guard at gate level, unlabelled. `physical.md:683` labels the same runs.

   **How I found them.** `git grep -i -E "usb_fs_dev|usb_ls_dev|test_usb_fs_device|test_usb_ls_device|usb_fs_data_demo|17\.6 kB|kB/s"` over all tracked `.md` files, each hit read in context. The others are labelled, are history (`claims-audit.md`), are references without figures, or are evidence READMEs that name their own run and commit. Under RULES ("a number in a doc needs a test or log behind it"), a figure for the current firmware with only an older firmware's log behind it needs the label.

2. **`docs/guide.md:57` still says "USB with line unit | 129 / core 0".** The table above it says the sizes "come from the generated board bundle". At 5554b88 the bundle's `USB_LINE` has 130 words (bc98a97: 129; counted from `tools/micropython/protoemu_fw.py`). The branch updated the same count in `pio-comparison.md` (×3), `v3-study.md` and `test_pio_comparison.py`, but not here.
   - `guide.md` was already out of step for UART RX (13 against the bundle's 14) and I2C (71 against 90) before this branch. Those two are not this branch's doing, but are worth fixing with it.

## Checked

**The firmware, against the ISA (`docs/isa.md` §JMP conditions 12 `txne`, 15 `!f k`; one tick per instruction without delay).**
- **The three cases at got_in:**
  - F_STAGED clear: `jmp !f` is taken to `nak_in`. That is the same count to `ldi r3, 0x5A80` as the old `jmp f` not taken: the NAK minimum is unchanged.
  - F_STAGED set over an empty FIFO: `jmp !f` not taken, `jmp txne` not taken, then `ldi`. One more instruction, so the NAK maximum is +1.
  - F_STAGED set with words in the FIFO: two jumps to `send_data` instead of one. IN → DATA is +1 throughout.
- **The checker agrees.** `python -m pe.timing firmware/usb_fs_dev.pasm --defs pe.usbdev:defines` at 5554b88 and on bc98a97's file: in_nak_ta `[7, 11]`→`[7, 12]` ticks (`[10, 15]`→`[10, 16]` clk), in_data_ta `[9, 13]`→`[10, 14]` (`[12, 17]`→`[13, 18]`). ack_ta, out_nak_ta, rx_bit and tx_byte are unchanged; all PASS against ≤ 26.
- **The evidence files.** Both outputs equal `evidence/model-usb-guard-20261104/pe-timing-usb_fs_dev-{bc98a97,c4003a2}.txt` once line numbers are removed. They shift by 2 because 5554b88 lengthened the header comment.
- **`usb_ls_dev`** has no `@point`/`@deadline` annotations (0 hits), as claimed. Its extra instruction is before `wait 1 pin DM`, ahead of two `[31]` nops, and `test_usb_ls_device` passes in RTL and under sigrok.
- **`txne` reads the core's own TX FIFO.** `usb_fs_dev` does not set `FJOIN_TX`, so `txne` is the TX FIFO as the ISA defines it, evaluated at the start of the cycle.

**Claim 3, and what the guard does, by swapping files in copies of 5554b88:**

| Copy | Result |
|---|---|
| 5554b88 as is | `test_usb_fs_data.py` + `test_usb_in_guard.py`: `83 passed`; my 040 every-access test: `52 passed`, every row 0 unanswered |
| bc98a97's `usb_fs_dev.pasm`, `usb_ls_dev.pasm` and `usbdev.py` | `6 failed, 58 passed`: exactly `every_access[8-after its token]`, `[9-after the SETUP]`, `[10-after the SETUP]`, and the three named points at FSET, after FSET and inside the pre-FSET check. `test_usb_in_guard.py`: `2 failed` |
| bc98a97's two firmwares, the new software (no restart) | `2 failed`: `[at FSET-after the SETUP]`, `every_access[9-after the SETUP]`. Removing the restart is only safe with the guard, and the two ship together |
| My mutant: the guard's `jmp !f F_STAGED` line removed, `jmp txne` kept | `76 failed, 151 passed` over `test_usb_fs_data`, `test_usb_in_guard`, `test_usb_fs`, `test_usb_ack` |

- **Mutants.** `test/mutants.py "usbfs-*" "usbdev-*" "fw-usb-*"`: `13 of 13 mutants killed`. That is the item's 11 plus two older `fw-usb-*` that the glob also picks up. `fw-usb-fs-no-in-guard` and `fw-usb-ls-no-in-guard` are killed by `test_in_with_f_staged_over_an_empty_fifo_is_naked[fifo-full]` and `[fifo-low]`.

**Claim 4.**
- **`remote_test.sh model test lockstep` at 5554b88:**
  - `model rc=0`, `2597 passed, 29 skipped`;
  - `test rc=0`, `TESTS=27 PASS=27` (`test_usb_ls_device`, `test_usb_fs_device`, `test_usb_concurrent_service` PASS);
  - `lockstep rc=0`, `TESTS=6 PASS=6`.
- **With sigrok-cli** (`SIGROK_CLI=~/protoemu-flow/sigrok/sigrok-cli`), in my git clone, `--ignore=test/model/test_swd.py`: `2625 passed, 1 skipped`. RTL `test_sigrok`: `TESTS=8 PASS=8`, including `sigrok_usb_ls_device` and `sigrok_usb_fs_device`.
- **`test_post_freeze` and `test_blind`** were not run. Neither loads `usb_fs_dev`, `usb_ls_dev`, `pe.usbdev` or the MicroPython bundle (`grep -n -i usb` on both files: no hits). The firmware is used by `test/test.py`, `test_sigrok.py`, the model tests and the board scripts, all run above.

**Claim 5's figures.**
- **RTL.** The `test` log at 5554b88 gives `turnaround ACK: 2.64 bit times = 10.5 clocks`, `NAK: … 13.4 clocks`, `DATA1: 3.98 bit times = 15.9 clocks`.
- **Model.** `test_usb_fs.py -s` gives ACK 10.9, NAK 12.7, IN → DATA 15.7 clocks, the same as `usbfs-model-c4003a2.log` line for line apart from the timing line.
- **`test_timing_check.py -s`:** `in_data_ta bus turnaround 15.73..16.45 clk; checker 13..18`, equal to `timing-check-c4003a2.log` apart from line numbers and paths.
- **The evidence README's 12 sha256 values** check (`sha256sum -c`).

**Claim 6.**
- **What the script does.** `usb_guard_run.py` checks `os.geteuid() == 0` and `fabric_guard.require_app("protoemu48")` before writing anything. It refuses if the firmware has no guard, and runs one demo at a time.
- **On the model:** `--model --attaches 2 --writes 5 --reads 2,3` printed `usb_fs_dev 142 words, got_in guard present`, 2 of 2 attaches ok, the zero-length steps `5 of 5`, `2 of 2` and `3 of 3` ok, and `PASS`.

**Claim 7.** `python -m pe.mpy_bundle --check`: `firmware bundle matches validated sources`, rc 0.

**Claim 8, by reading (asked).**
- **In the two-core low-speed device:**
  - `usb_ls_rx.pasm:209-214` `got_setup` clears F_STAGED, F_GOTACK and F_SENT, but has no flush of the TX core's FIFO;
  - `usb_ls_tx.pasm:22-35` sends whatever is in its FIFO when F_IN finds F_STAGED;
  - `pe.usbdev.DeviceSoftware.handle` (`usbdev.py:242-247`) clears the TX core's FIFO only when it processes the SETUP's record.
- **So the race is real.** A re-stage whose FSET write lands after the SETUP token, inside 040's window (the pre-FSET check's RX-level read to the FSET write), sets F_STAGED again over the old packet, which is still there. The next IN (for SET_CONFIGURATION, the status IN) takes it as stale DATA. The guard doesn't apply, since the FIFO is not empty.
- **How long it stays open.** The post-FSET check clears F_STAGED about three SPI accesses later, as the item says; `handle()` clears the FIFO at the next service.
- I have not run it: `SpiBridge` drives only the full-speed host, as the item says.

## Notes

1. **A rule slip of mine: SWD.** My first sigrok-cli run of the model suite was `pytest test/model` in an archive copy, without `--ignore=test/model/test_swd.py`. So it also collected and ran the SWD model test. RULES.md says no SWD work of any kind. It was model-only, no hardware and nothing of SWD's touched or changed, and I did not use or read its results. The figure reported above is the rerun with the file excluded. I have not run anything SWD-related since. (That first run's 7 `test_flow_tools` failures were my archive copy having no `.git`; in the git clone they pass.)
2. **The replay comparison has no committed command.** `compare-dev-replay.txt` and the two `replay-*.log` say ALL PASS, the same requests, re-stage count 1, 74 wire packets, and "pads equal False". `run.sh` doesn't make them, and the README doesn't give the `usb_replay_demo.py` command or the comparison script. I did not rerun them. The kv260 README rests the "board replay needs fresh plans" sentence on them.
3. **What the guard does not cover, from reading** (untested, probably rare). It tests for an empty FIFO only. If a SETUP's flush landed between two pushes of a normal, non-racing stage, the rest of the words would arrive after the flush, and F_STAGED would then be set over a partial packet. The core would take a data word as the bit count, with the guard as before it. That stage has no checks around FSET (only the re-stage does), and `handle()` clears F_STAGED only at the next service. I did not find a test that reaches it. This would go with claim 8 in the new backlog item.
4. **The board script leaves its trace settings behind.** `Tracer.close()` clears `set_event` but leaves `buffer_size_kb` at 16384 and `tracing_on` at 0 on the Kria. That is harmless, but worth a line in the orchestrator's notes.

## What I ran

**On runner1:**
- `remote_test.sh model test lockstep` at 5554b88: as above. Checkout `agent-runs/protoemu-verify-5554b88-2626905`, removed.
- In `~/verifier-044/`:
  - the copies in the table (archive copies of 5554b88 with bc98a97's files swapped in, and my mutant);
  - `test_v040_allops.py`;
  - `test/mutants.py "usbfs-*" "usbdev-*" "fw-usb-*"`;
  - `test_usb_fs.py -s`, `test_timing_check.py -s`;
  - `pe.mpy_bundle --check`;
  - `usb_guard_run.py --model`;
  - the model suite with sigrok-cli (and the slip in note 1);
  - RTL `test_sigrok`.

**Locally:**
- `pe.timing` at both commits;
- the diff;
- the firmware read against `docs/isa.md`;
- `usb_ls_rx`/`usb_ls_tx`/`usbdev.py` for claim 8;
- the doc grep for finding 1;
- word counts of the bundle at both commits;
- `sha256sum -c` of the evidence.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria, and nothing was pushed.
