VERDICT: PASS

Commit 851a373, base 8a6dd8f. Every blocking finding from 019 and the should-fix in 020 is fixed. The guard now refuses everything I fed it except one of our apps shown active with every kernel file agreeing. The board's real rows behave as the item asks. Fakes only; the Kria was not touched.

| Claim | Result |
|---|---|
| 1. Exact table, one active row, a known name; any other line refuses | PASS. The captured lines tested below |
| 2. firmware-name, state and the installed bitstream all required | PASS. The firmware-name judgement is below |
| 3. `PE_ACTIVE_APP` and `--active-app` removed | PASS |
| 4. `hw-*` mutants 71/71; model 1910 | PASS, both reproduced. My own set: note 3 |
| 5. A board that answers is never power cycled; `recover` never expires; load one command at a time; 20 mA cites DS925 | PASS for step 0, the launcher and DS925. Four other places still chain the load (should-fix 1) |

## The board's real rows

I fed `fabric_guard.check()` the item's header and `protoemu40` row exactly as given, spacing and the `id_ok` Pid included. The kernel files were fakes in a temporary directory.

| Input | Result |
|---|---|
| Header and `protoemu40 ... (0+0+0) -1` | refused: "shows no active app", with the load sequence |
| The same plus `k26-starter-kits` active, ending `(0+0+0) 0->0,` | refused: "'k26-starter-kits', not one of ours" |
| The `protoemu40` row in the active shape, ending `0->0,` | **passes**, `'protoemu40'` |
| That, plus `k26-starter-kits` inactive | passes |
| `protoemu: Load Error: -1` before an active table | refused: "isn't a row of its table" |

So the real rows are not refused. They can be refused if the real active row has no Pid value, or more than one `n->n,` slot. The doc's open-questions table says the first; that refusal is safe.

## Claim 2: firmware-name

I agree, also from memory and not checked on this board:
- Removing an overlay reverts its changeset. A property the overlay added to a base node, which is what the boot log's "memory leak will occur if overlay removed" warning says happened, is removed from the live tree, and so from `/proc/device-tree`.
- The FPGA region programs the device in the overlay's pre-apply notifier. If programming fails, the apply is abandoned before the changeset goes in, so the property never appears.
- So "present and equal to `<app>.bit.bin`" means our overlay was applied and programmed. If the base tree ever carries its own `firmware-name`, a revert restores that value instead, which still refuses.
- `install.sh:33` sets the name to `$APP.bit.bin`, and `protoemu.dtsi` targets `&fpga_full`. The boot log puts that at `/fpga-region`, which is the guard's path.

Each piece refuses when it is missing or wrong, and names the path. Tested with my fakes: firmware-name missing, state missing, bitstream missing, state `write error`. Also: `protoemu` active while firmware-name says `protoemu40.bit.bin`. A NUL-terminated name with a trailing newline passes, as it should.

## Should fix

1. Claim 5's "the load is one command at a time" holds in step 0 and in `hw_day.load_line`, but not everywhere:
   - `docs/hardware-day.md:1176`, `:1316`, `fpga/kv260/host/usb_demo.py:343` and `usb_fs_data_demo.py:144` still print `sudo xmutil unloadapp; sudo xmutil loadapp ...`.
   - The guard's own `SEQUENCE` says "never put them on one line", so the runbook contradicts itself.
   - It is not dangerous: no tool follows on the line, and the next tool goes through the guard. Make them match.

2. The tests don't contain the captured lines. `test_fabric_guard.py:24` builds rows with Pid `0` and two-space gaps. The item quotes the board's real header and row, with `id_ok` and wide spacing. Put those two lines in a test verbatim, so a later change can't refuse the real format without a test failing. My probe above does this, and it passes.

## Notes

3. My 17 mutants against `test_hw_day.py` and `test_fabric_guard.py`: **11 killed.** The six survivors:

   | Survivor | Judgement |
   |---|---|
   | Active name read from the Base column | Equivalent here: flat apps have base = name, in the fakes and on the board |
   | Header matched on `Accelerator` alone | Test gap. Claim 1 says "the real header", but only the old six-column format is tested, and its rows refuse anyway |
   | `"operating" in state` | Test gap; no real kernel state contains "operating" except `operating` |
   | `firmware-name.startswith(app)` | **Test gap with a real case:** `protoemu` active while firmware-name says `protoemu40.bit.bin` or `protoemu48.bit.bin` would pass. The code compares exactly and refuses, as my probe shows. No test covers an app name that is a prefix of the file name |
   | `hw_day.check_app` without its `APPS` check | Equivalent: `test_hw_day.py:487` asserts `APPS == KNOWN` |
   | `recover`'s "every wire out" line removed | Printed text only |

   Killed: NUL kept in the file text, two headers allowed, `k26-starter-kits` added to `KNOWN`, the bitstream directory accepted without the file, a timeout uncaught, `OSError` uncaught, a missing state accepted, `recover` accepting a future time, a JSON `true` accepted, every step never expiring, and the load line without its "stop".

4. Some text still describes step 0 as an unconditional power cycle: `docs/hardware-day.md:225` ("Recovery: the one power cycle, by you") and rule 8 (`:50`). Step 0 itself now begins "If it answers, do not power it off". Add "only if it doesn't answer" in both places.

5. Two minor parse looseness points, both caught by the kernel checks:
   - A row before the header is accepted.
   - The middle columns are not checked, so any seven tokens with the right last two pass.

   The builder states the second.

## What held

- **Claim 3:** `git grep` finds `PE_ACTIVE_APP`, `--active-app` and `told` only in the tests that assert their absence (`test_fabric_guard.py:123-127`, `test_hw_day.py:491`). `fabric_guard.py` reads no environment. `hd off` no longer offers an override.
- **020 should-fix:**
  - `recover` is `once=True`. `fresh()` takes a month-old `recover` and still refuses `True`, NaN, a string and a future time (test, and my mutants).
  - Step 0 asks "does it answer?" first. The power cycle is under "Only if it doesn't answer for 10 minutes", and `test_hw_day.py:626` checks that order.
- **019 finding 5:** step 0 has the person rename `~/protoemu`, `~/protoemu40` and `~/protoemu48` so nothing runs from them, and `recover` lists them with `ls -ld`.
- **019 finding 6:** "probably only a power cycle", and the mechanism "likely, not proved". The cause is now attributed to the board's own journal, read by the orchestrator.
- **DS925:** the absolute maximum ratings page lists "IDC: Available output current at the pad, -20 to 20 mA" (and IRMS the same). The citation is right.

## What I ran

- `remote_test.sh model` at 851a373: `1910 passed, 28 skipped in 438.31s`, rc 0.
- On runner1 in `verifier-022/`, since removed:
  - `python3 test/mutants.py 'hw-*'` with the test venv on PATH: `71 of 71 mutants killed`, rc 0.
  - My 17 mutants (note 3), each in a fresh copy, with a check that each one applied. Baseline of the two files: `188 passed`.
- The probe of 22 inputs to `fabric_guard.check()` locally, with fake kernel files under my scratch directory. No `/dev/mem`, no board.
