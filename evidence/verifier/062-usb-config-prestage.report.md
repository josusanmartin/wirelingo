VERDICT: PASS

Item 062 `usb-config-prestage`: commits f3b51a6, 541f806, 988746a and ab2db8e on f8eed52, checked out detached at ab2db8e. I read the diff (18 files, +1016 −43) and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

The firmware change is right against USB 2.0 and the ISA, and its tests are strong: my own 9 meaningful mutants of the new path all die. The A/B script follows the board rules, and its statistics are correct. Model, cocotb and mutant runs all pass. The evidence level is stated as model and RTL simulation, with the board result pending.

Two notes follow: the flag survives a bus reset (harmless, cheap to harden), and the A/B run will take hours. Neither blocks.

## Findings

None blocking.

### Notes

1. **F_CFGZLP (flag 7) survives a bus reset.**
   - The firmware's `bus_reset` (`usb_fs_dev.pasm:267-271`) doesn't clear it.
   - `DeviceSoftware.bus_reset` clears flags with `A_FCLR` `0x3F` and then `0x7F` (`tools/pe/usbdev.py:227`, `:238`), never bit 7.
   - So after a SET_CONFIGURATION whose status the firmware answered, the flag is still set when the host resets the bus.
   - This is harmless in every legal host sequence. Every control transfer starts with a SETUP, whose token clears the flag in `got_setup` before an IN can reach `got_in`, and no IN to endpoint 0 is valid before it.
   - Cheap hardening: `0xFF` in `bus_reset`'s second write, or `flag clr F_CFGZLP` in the firmware's `bus_reset`.
2. **Every SET_CONFIGURATION value gets its status answered, valid or not.** USB 2.0 §9.4.7 asks a device to STALL a configuration value it doesn't have. The firmware answers any 0x00/0x09 SETUP, whatever its wValue. That is unchanged behaviour: before this branch the software staged a ZLP for every SET_CONFIGURATION too (`usbdev.py:264-265` at f8eed52). Not a regression.
3. **The A/B run's length.** 300 pairs means 600 single attaches. Each is a full `usb_fs_data_demo` run with a 7 s configure timeout and a 120 s guard, so a whole run will take hours. The orchestrator should plan for that. The script can be stopped cleanly at any point (below).

## The firmware against USB 2.0 and the ISA

- **Answering before the software applies the configuration: correct for this device.**
  - The configuration descriptor (`usbdev.py:39`) is one configuration with one interface and bNumEndpoints 0 (byte 13). The demo's `LoopbackDevice` uses the same `usbdev.DESCRIPTORS` (`usb_fs_data_demo.py:204`, `:293`) and moves its data over endpoint 0.
  - So configuring changes no endpoint state, and no data toggle needs resetting. `DeviceSoftware` records nothing for SET_CONFIGURATION either before or after the change.
  - `LoopbackDevice.handle` hands SET_CONFIGURATION to the base class (`usbfsdata.py:48`), so the new rule applies there too.
- **DATA1 is right, including on a repeated IN.**
  - A no-data control transfer's status stage is a zero-length DATA1 (§8.5.3).
  - The packet is `ldi r3, 0x4B80` with `out line, 32`: SYNC 0x80, PID 0x4B (DATA1: 1011 with its complement 0100), and 16 zero bits, the CRC16 of an empty payload.
  - The flag isn't cleared on the host's ACK, so a repeated IN gets the same DATA1. That is what a device must do when the host retries, and `test_status_in_answered_with_the_software_held` sends two INs and checks both are `(DATA1, b"")`.
- **The flag's clear paths.**
  - Every SETUP token to our address runs `got_setup`, whose second instruction is `flag clr F_CFGZLP`, before the SETUP's DATA can set it again.
  - Bus reset: note 1.
- **The 16-bit peek can't misread another request.**
  - After the PID, `mov isr, null` empties the ISR (`usb_fs_dev.pasm:90`), and `autopush 24` can't fire within 16 bits.
  - With `.shift_in right`, the 16 bits sit in ISR[31:16], so the low half of `::isr` is `rev8(bmRequestType) << 8 | rev8(bRequest)`. For 0x00, 0x09 that is 0x0090.
  - `cmp r1, 0x90` compares all 16 bits of the register. The test plants HID SET_REPORT (0x21 0x09), vendor 0x40 0x09, endpoint 0x02 0x09, SET_FEATURE and SET_ADDRESS. All are NAKed with the software held (`test_other_requests_wait_for_the_software`, before and after a SET_CONFIGURATION).
  - Short packet: `jmp t, dend` inside the peek leaves before the compare, so no flag.
  - Bad CRC: the flag may be set, but there is no ACK, and the host must repeat the whole SETUP, token first, which clears it (`test_a_bad_setup_leaves_no_answer_behind`). The same holds for a SETUP followed by a DATA1, which `dend` rejects.
  - The peek runs only with F_SETUP set (`jmp !f F_SETUP, dbit`), so OUT data is never peeked.
  - The 16 peeked bits still go through the CRC tap and into the record, as before.
- **Cycle counts, one instruction per tick (isa.md §4.1, CLKDIV 1):**
  - IN → NAK: `nak_in` gains exactly one instruction (`jmp f F_CFGZLP, cfg_zlp`, not taken) before `ldi r3`. That is +1 tick, as 13.4 → 14.4 clocks says.
  - The ZLP path: `got_in` (1) → `nak_in` (1, taken) → `ldi r3` (1) → `wait 1 pin DM` → `mov osr, r3 [4]` (5) → `out line, 32`. That is the same count to the wait and after it as the NAK path (`ldi`, `wait`, `mov [4]`, `out`).
  - `python -m pe.timing firmware/usb_fs_dev.pasm --defs pe.usbdev:defines` gives `in_zlp_ta` and `in_nak_ta` both `[8, 13] ticks … with adjust +3..4: [11, 17] clk`, PASS against 8-26. All seven deadlines pass, rc 0.
  - The 14.1 against 14.4 clocks in RTL differ by bus phase, not instructions.
  - The peek's loop is 4 instructions per bit at 4 clocks per bit (`in line`, `jmp t`, `add`, `jmp r1--`). That is where the line unit's extra held bit comes from, and the 3-instruction `dbit` loop recovers it, as the builder states. `test_setup_capture_keeps_up` (±2500 ppm) covers it.
- **Size:** 159 words. I assembled at ab2db8e with `usbdev.defines()`; `cfg_zlp` is a label.

## The race-test change, and mutants

- **What changed.** At full speed, `restage_race` and `abort_fs` now abandon the transfer with a zero-length REQ_WRITE instead of SET_CONFIGURATION. The software's restage guards (`setup_since`, the FCLR of F_STAGED|F_GOTACK, `dirty`) don't look at the request. So the races they exercise are the same, while SET_CONFIGURATION's status no longer goes through the software. Low speed still uses SET_CONFIGURATION, since `usb_ls_*` is unchanged. `staged_over_empty` now waits for two more `service()` calls to end before checking.
- **Does a mutant now survive that died before? No.** I reran 134 mutants: every `usb*`, `fw-usb*` and `hw-*` one (116, the builder's set), plus all 18 cocotb (`make`) mutants, since `test/test.py` and the firmware changed. Result: 134 of 134 killed, with no stale, survived, infrastructure or failed-baseline result. That includes the 7 new ones:
  - `fw-usb-fs-no-cfg-prestage`, `fw-usb-fs-cfgzlp-kept`, `fw-usb-fs-cfgzlp-any-request` and `usbdev-cfg-zlp-staged-too`, killed by the new prestage tests;
  - `usbfs-zlp-staged-twice-counted`, killed by `test_zero_length_write_stages_its_status_once`;
  - `hw-ab-arms-unchecked` and `hw-ab-stall-as-other`, killed by `test_refuses_arms_the_wrong_way_round` and `test_classify`.

  `usbdev-restage-after-setup` and `usbdev-dirty-not-cleared` are killed by `test_no_restage_after_a_setup`. Every mutant whose file or killing test changed is in that set. At f8eed52 all 234 were killed (058), so none has regressed.
- **My own firmware mutants** (`fwmut.py`, each in a throwaway copy, against `test_usb_config_prestage`, `test_usb_fs`, `test_usb_fs_data`, `test_usb_stage_abort` and `test_timing_check`): 10 of 10 killed.

  | Mutant | Killed by |
  |---|---|
  | `peek-low-byte-only`: compare bRequest only | the vendor 0x40 0x09 case |
  | `peek-high-byte-only`: compare bmRequestType only | the SET_FEATURE case |
  | `peek-15-bits` | the held-software and concurrent tests |
  | `peek-on-out-data-too`: drop the F_SETUP test | `test_fs_enumeration`, `test_fs_rx_jitter_and_rate` |
  | `zlp-data0`: PID DATA0 | the held-software and concurrent tests |
  | `zlp-before-staged`: firmware ZLP even over a staged packet | `test_linux_enumeration_changes_only_the_set_configuration_status` |
  | `zlp-no-crc-bytes`: `out line, 16` | the held-software and concurrent tests |
  | `no-clear-at-setup` | the other-requests and concurrent tests |
  | `software-also-stages`: `DeviceSoftware` stages its ZLP too | the held-software and concurrent tests |
  | `every-setup-sets-flag` | not a real test of behaviour (below) |

  `every-setup-sets-flag` is behaviourally equivalent: I put the set before the existing clear. It died only because `test_the_software_knows_the_firmware_answers` builds a firmware without the pre-stage by text edits, which my extra line broke.

## The A/B script, for board safety

`fpga/kv260/host/usb_prestage_ab.py`, read in full.

- **Refusals before anything is written or opened.**
  - Without `--model`, it exits unless run as root and unless `fabric_guard.require_app("protoemu48")` passes (one active app).
  - It loads no app, so RULES' unload/load/listapps sequence stays the orchestrator's.
  - It then refuses unless A's `usb_fs_dev` has no `cfg_zlp` and B's has. Both are assembled from each tree's own `firmware/usb_fs_dev.pasm`.
- **Board rules.**
  - The script touches no PMOD pin, eth0, reboot or power control: `grep` for `reboot`, `poweroff`, `shutdown`, `eth0`, `pmod`, `xmutil`, `loadapp`, `ip link` and `ifconfig` finds none in it or in `usb_guard_run.py`.
  - It runs each arm's own `usb_fs_data_demo.py`, one process at a time. That demo is byte-identical at f8eed52 and ab2db8e (no diff), and the orchestrator already runs it.
  - The only change to `usb_guard_run.py` is an optional `script=None` argument to `demo()`, which keeps the old default.
- **Stop paths.**
  - `g.Stopper` catches SIGINT, SIGTERM and SIGHUP. The running demo gets SIGTERM and 60 s to detach, then SIGKILL, as `usb_guard_run.demo` already does.
  - The loop breaks, the `finally` closes the capture, the summary is written with STOPPED, and the exit is 128 + the signal.
  - No device in the first five attaches gives NO DEVICE, exit 3.
- **ABBA order:** `order()` gives A B, B A, … for `--pairs` pairs (`--pairs` attaches per arm).
- **Classification is symmetric.** The demo's "not configured (…); requests seen: [...]" line (`usb_fs_data_demo.py:424`) is the same at both arms' commits, and `classify` matches it ("stall" only when SET_CONFIGURATION was the last request seen).
- **Statistics: correct.**
  - Over 300 random cases, `fisher_less` equals an independent one-sided hypergeometric sum computed in exact fractions (largest difference 0).
  - `--plan` gives power 0.18, 0.72 and 0.95 at 100, 200 and 300 attaches per arm when B never stalls, as the item says.
  - The 3 % base rate is the doc's 14 of 436.

## The builder's claims

1. **The old path: holds.** At f8eed52, `got_setup` flushes the TX FIFO and clears F_STAGED, so only the software can stage the status ZLP, after its poll.
2. **The new path: holds,** with notes 1 and 2.
3. **159 words: holds.**
4. **RTL costs:** the static bounds hold. I checked the RTL figures as consistent with the instruction counts. I didn't re-measure them separately: the cocotb run below includes `test_usb_fs_config_zlp`, which asserts its turnaround inside 2-6.5 bit times.
5. **The double-stage test: holds.** `test_zero_length_write_stages_its_status_once` and its mutant `usbfs-zlp-staged-twice-counted` are killed (in the run above).
6. **`staged_over_empty`: holds.** It now waits for two `service()` ends before checking.
7. **Race tests: hold.** `usbdev-restage-after-setup` and `usbdev-dirty-not-cleared` are killed (in the run above).
8. **Tests: hold.**
   - Model suite at ab2db8e: 2914 passed, 29 skipped.
   - cocotb `test` at ab2db8e: 28 of 28 (`TESTS=28 PASS=28 FAIL=0 SKIP=0`, make rc 0). 988746a..ab2db8e changes only `docs/usb-full-speed.md`.
   - `pe.timing`: rc 0.
   - I didn't run `test_usb_uart_rtl`. Its firmware (`usb_uart_*` with `usb_ls_*`) and its test are unchanged.
9. **The A/B script: holds** (above).
10. **Docs: hold.** `fpga/kv260/README.md` says "evidence: model and RTL simulation, not yet on the board" and that the change "may not change the rate". `README.md` and `explain-wirelingo.md` say tested on model and RTL, with the board A/B not run. The 159 words, 300 attaches per arm and the 0.95 / 0.72 powers match the script.

## Asked checks

- **Merge.** `git merge-tree --write-tree` onto 088d703 is clean. It is also clean together with 063 (`present3`) in either order (063 report).
- **Privacy.**
  - The changed files' added lines and the four commit messages have 0 hits for:
    - the provider and mail-provider names, the hostname pattern;
    - home paths;
    - the tailnet label class, `.ts.net` names, 100.64/10 and the IPv6 prefix;
    - emails other than examples.
  - Each message ends with the Co-Authored-By line.
  - The A/B script's default log folder is `~/usbab/…`, a home-relative path, not a private one.

## What I ran

- **Model suite.** `remote_test.sh model` at ab2db8e (runner1; `--ignore=test/model/test_swd.py` is built in): rc=0, "2914 passed, 29 skipped in 1406.70s (0:23:26)", with "SIGROK CHECKS SKIPPED: 28 tests".
- **cocotb.** In a runner1 clone at ab2db8e, `make SIM_BUILD=sim_build/v062` in `test/`: `TESTS=28 PASS=28 FAIL=0 SKIP=0`, make rc 0.
- **Mutants.** `python test/mutants.py <134 names>` in the clone: "134 of 134 mutants killed", rc 0.
- **My firmware mutants.** `fwmut.py`: 10 of 10 killed (one equivalent; see above).
- **Static timing.** `pe.timing` locally: all deadlines PASS, rc 0.
- **Statistics.** `usb_prestage_ab.plan()` and the Fisher cross-check, locally.
- **Reading and diffs.** The firmware, `usbdev.py`/`usb_device.py`, `usbfsdata.py`, the descriptors, the demo at both commits, the A/B script and `usb_guard_run.py`.
- **Privacy.** Counts over the added lines and the commit messages.
