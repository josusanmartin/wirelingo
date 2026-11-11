VERDICT: FAIL

072 pkg3-firmware (branch `ao-pkg3`, `32b2cee`, the test.py half of `817c953`, and `3a7feab`; backlog AO): verifier report.
Kria: verifier does not touch it. No SWD.

`eth10_tx_fcs`'s frame and FCS are right tick by tick, and so is everything else I checked:
- the in-engine loopback passes on the model and the RTL;
- `usb_enum_ep1`'s static bounds and measured turnarounds are as claimed;
- the 6 mutants are killed;
- the word counts match.

The verdict is FAIL on one false claim. `eth10_tx_fcs` is not "everything else eth10_tx's": after every frame its next link pulse comes **104.86 ms** later instead of within 16 ms (finding 1). That breaks 802.3's 16 ± 8 ms link-pulse spacing and can make a link partner drop the link. The fix is one register choice, a word or less.

## Findings

1. **`eth10_tx_fcs` leaves r1 at 0xFFFF after a frame, so the next link pulse is 105 ms late** (`firmware/eth10_tx_fcs.pasm:37`, `:66`, `:74`, `:31-34`).
   - **The cause:** `idle_wait` uses r1 as its link-pulse countdown: `ldi r1, 9999`, then `jmp r1--, idle_wait` every 64 ticks, which is 16 ms. The new FCS loop reuses r1 as its bit count (`ldi r1, 31`, then `jmp r1--` per FCS bit). The last FCS bit's `jmp r1--` finds 0, falls through and decrements to 0xFFFF. `tp_idl` then returns to `idle_wait` with r1 = 0xFFFF, so the next pulse comes after 65,536 × 64 ticks. In `eth10_tx`, r1 is untouched by a frame and keeps the remainder of the idle count.
   - **Measured on the model** (`nlp_after_frame.py`, runner1, test_eth10's line at 40 MHz, 25 ns ticks, one 64-byte frame):
     - "eth10_tx: frame end -> next link pulse 16.00 ms ; pulse-to-pulse 16.00 ms";
     - "eth10_tx_fcs: frame end -> next link pulse 104.86 ms ; pulse-to-pulse 16.00 ms".
   - **Why it matters:** 802.3 clause 14 sends normal link pulses 16 ± 8 ms apart while idle. A receiver's link-loss timer is 50–150 ms, so a compliant partner may declare the link down after a frame followed by idle. That is my reading of the standard; the model shows the 104.86 ms.
   - **Why the tests miss it:** they send frames back to back or step only a little past a frame. `test_core1_tx_to_core0_rx_with_link_pulses` puts pulses between frames, but no test measures the first pulse after a frame against 24 ms.
   - **Fix:** count the FCS bits in r2. It is free after the preamble (`ldi r2, 30` then `jmp r2--, pre` leaves it at 0xFFFF before the frame bits, and nothing after uses it). Or reload r1 in `tp_idl`, for example by splitting `nop side 1 [9]` into `ldi r1, 9999 side 1` plus `nop side 1 [8]`, which keeps TP_IDL's 10 ticks and the word count at 52 or 53.
   - **Add a test:** after a frame, the next link pulse comes within 8–24 ms. The `fw-eth10-*` mutants would then need a sibling for this.

2. **`usb_enum_ep1`: one clock of static margin at full speed. It is correctly stated and guarded, so it is a note only.**
   - **The static bound:** `pe.timing` with `-D DP=0 -D DM=1 -D TURN=4` (run locally, rc 0):
     - `ep1_ta` is "[22, 23] ticks … with adjust +-3..2: [19, 25] clk", against "<= 26";
     - `in_ta_any` is [10, 25];
     - `data_ta` is [9, 18];
     - `rx_bit` is 3;
     - `in_reach` [8, 18], `hs_reach` [8, 12] and `e1_reach` [17, 18] ticks are all ≥ 7.
   - **Against `usb_enum`:** next-8x4's `usb_enum` gives `in_ta_any` [10, 21], so 5 clocks of margin. 26 clocks is the 6.5 bit times at 48 MHz.
   - **Dynamic guards:**
     - On the RTL at 3 and 2 cores, `test_usb_autonomous_ep1_fs` measured "33 transactions, turnaround 3.25-5.75 bit times". That is 13–23 clocks, inside the static [19, 25] for endpoint 1. It asserts `2.0 <= t <= 6.5` (`test/test.py:1339`).
     - The model test asserts the same (`test_usb_enum_ep1.py:88`).
     - `test_deadlines_static_and_sound` holds the static bounds.
   - **The risk:** the adjust range −3..2 assumes 3–4 clocks of pads and 2–6 ticks to see the J. One more clock anywhere on endpoint 1's path, or in the silicon input path, and the static check fails. The builder's caveat says so.
   - **Suggestion:** keep the timing sidecar's `ep1_ta` as a required (not conditional) deadline in CI, so that a later edit can't erode the clock silently.

## Asked checks

- **`eth10_tx_fcs.pasm`, tick by tick** (side 1 = H, side 2 = L; a 1 is L then H, a 0 is H then L; 4 ticks per bit).
  - **Data loop:** identical to `eth10_tx` (`zero` H H L L, `one` L L H H, `out r3, 1` at tick 3 fetching the next bit). Each `out r3, 1` feeds the OUT tap. CRCCTRL `0x00AF1DB7` decodes to W = 32 (CRC_W = 0x1F, `[23]` set) and OUT_TAP; POLY_HI `0x04C1` makes the polynomial 0x04C11DB7.
  - **The preset:** `mov crc, ~null` before the preamble. The preamble and SFD are `nop`s and feed nothing.
  - **Last data bit, a 0** (`zlast`, from `zero`'s tick 1 `jmp !r0`), ticks 2–4:
    - tick 2, H: `mov osr, ::crc`. The last data bit was fed by the previous block's tick-3 `out`, so the CRC is final.
    - tick 3, L: `out r3, 1` takes OSR[0] = CRC[31]. Shift right, so x^31 goes first.
    - tick 4, L: `jmp r3, fzero`. CRC 1 sends FCS 0; CRC 0 falls into `fone`, FCS 1. So the complement costs no tick.
  - **Last data bit, a 1** (`olast`): L `mov osr, ::crc`, H `out r3, 1`, H `jmp !r3, fone`. The same, mirrored.
  - **FCS blocks:**
    - `fone`: L `jmp r1--, fo2`, L `out r3, 1`, H `jmp r3, fo_z`, H `jmp fone`/`jmp fzero`. That is L L H H.
    - `fzero`: H H L L.
    - Each continuing block fetches the next CRC bit at tick 2 and branches on it at tick 3. Every path is 4 ticks.
  - **The 32-bit count:** `ldi r1, 31`. FCS bit k sees r1 = 32 − k at its tick 1, so bits 1–31 continue and bit 32 falls through. There is 1 `out` in `zlast`/`olast` and 31 in the continuing blocks, 32 in all. The OSR then holds no bits, so `jmp !osre, flush` is never taken, as commented. ✓
  - **TP_IDL:**
    - after a final FCS 1: L, L, `jmp tp_idl side 1 [1]` (2 H), then `nop side 1 [9]` (10 H), 12 ticks = 300 ns;
    - after a final 0: H, H, L L, `jmp tp_idl side 1` (1 H) + 10 = 275 ns.

    Both match `eth10_tx`'s `olast`/`zlast` exactly. ✓
  - **A part-used last data word** is overwritten by `mov osr, ::crc`, which replaces `eth10_tx`'s flush. ✓
  - **The exception** is finding 1, r1 after the frame.
- **Model tests.** `test_eth10_fcs` and `test_usb_enum_ep1` are in the model suite at the tip (`3a7feab`, my runner1 clone, `remote_test.sh`'s pytest flags but `-n 8`, `--ignore=test/model/test_swd.py`): rc 0, "3000 passed, 29 skipped in 1196.48s (0:19:56)", as claimed; SIGROK checks skipped: 28, and a pass says nothing about them.
- **The two `test.py` tests** (runner1, my clone at `3a7feab`, run inside the full `make`):
  - 3 cores: `test_usb_autonomous_ep1_fs` PASS, `test_eth10_fcs_in_engine_loopback` PASS;
  - 2 cores (`PE_NCORES=2 PE_NBANKS=2`): both PASS;
  - logged: "usb_enum_ep1 (full speed): 33 transactions, turnaround 3.25-5.75 bit times".
- **The timing checker.** Finding 2. The output matches claim 4's bounds exactly.
- **Mutants.** `test/mutants.py` at `3a7feab` on runner1, every baseline passed. All 6 of 6 are killed:
  - `fw-eth10-rx-fcs-residue` by `test_good_frames_pass_with_jitter[0]`;
  - `fw-eth10-tx-fcs-31-bits` and `-not-reversed` by `test_tx_fcs_min_frame_from_fifo`;
  - `-no-preset` by `test_tx_fcs_streamed_and_back_to_back`;
  - `fw-usb-ep1-data-as-ep0` and `fw-usb-ep1-toggle-stuck` by `test_reports_pins_on_endpoint_1[low]`, the latter in 12 s, so the host model's bound works.
- **Word counts** (my assembly of `3a7feab`): `eth10_rx_fcs` 27, `eth10_tx_fcs` 52, `usb_enum_ep1` 255; `eth10_rx` 10, `eth10_tx` 34, `usb_enum` 253. ✓ The longest program fits the 256-word bank.
- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `3a7feab`: 727 files, 14 messages, "TOTAL flagged: 0".
  - My private-string check over the branch diff and messages: "TOTAL 0".
  - The noreply identity is on every commit.

## What I ran

- **runner1** (`~/protoemu-flow/verifier-070/`, no harden):
  - the top-level suite at 3 and 2 cores;
  - `test/mutants.py` with the 6 names;
  - the model suite at the tip;
  - `nlp_after_frame.py`: the model, test_eth10's `Line` and this branch's `FcsLine`, one frame each, measuring to the next TD+ pulse.
- **Locally:** reading the three programs and their tests, `pe.timing` on `usb_enum_ep1` and on `usb_enum` (`d2ed344`), and the word counts.

Summary: the in-engine FCS is right on the wire and `usb_enum_ep1` meets its turnaround with a guarded 1-clock margin. `eth10_tx_fcs` breaks the idle link-pulse spacing after every frame, 104.86 ms against 16 ± 8. Count the FCS in r2 (or reload r1), and add a post-frame link-pulse test.
