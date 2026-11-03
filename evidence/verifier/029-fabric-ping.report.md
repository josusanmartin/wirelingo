VERDICT: FAIL

Commit da82a51 on base 97e442d (an ancestor), five commits. No `src/` or `flow/` change.

The board run itself holds up:
- The archive's sha256 matches `git archive --format=tar.gz da82a51 tools firmware fpga/kv260/host` (`735de20a…`).
- My own `--compare` of the two evidence files gives SAME.
- The model suite and the 94 mutants reproduce.

It fails on two claims about the code:
1. The board is not given back on every exit path. Nothing gives it back if the run ends while the board rig is being built.
2. `--compare` does not compare every TD edge. It never looks at the link pulse, or at anything outside the frame's own burst.

Both fixes are small. Neither changes what the board run showed.

| Claim | Result |
|---|---|
| Model suite 2360 passed, 28 skipped | PASS: `2360 passed, 28 skipped in 793.48s` |
| `mutants.py 'hw-*' 'fab-*'` 94/94 | PASS: `94 of 94 mutants killed` |
| Fake KV260 runs the board code path, to the clock | PASS |
| PMOD registers only ever 0 | PASS |
| Board handed back on every exit path (errors, Ctrl-C, SIGTERM, SIGHUP) | **FAIL** (finding 1) |
| Board probe PASS; `--compare` SAME (947 / 939 / 917 edges, phases 1 / 4 / 0) | PASS, reproduced from the evidence files |
| "SAME really compares every TD edge" | **FAIL** (finding 2): the frame burst's edges, not the link pulse |
| Board selftest 5/5 ping, UDP echo, no-reply cases, `"pass": true` | PASS: as logged in `selftest.log` |
| Docs' evidence level not more than the evidence | PASS. The docs say "model" (note 1) |
| Zero-word restoration can't hide a receive error | PASS with one stated blind spot (note 2) |
| Guard parser fails closed on unknown lines | PASS |

## Blocking

1. **The board is not given back if the run ends while the board rig is built.** The claim is in `docs/eth10-network.md:353-355` ("On any end (normal, exception, Ctrl-C, SIGTERM, SIGHUP) the board is given back") and the `eth_tap.py` docstring.
   - **Why.** `main` builds the backend at `eth_tap.py:682` and `:706` (`backend = make()`). Only after that does `run_closing` install the handlers and the `finally: backend.close()`. `make()` goes through `fabric_eth.board_rig` (`fabric_eth.py:336`, `pfd.HwRig()`).
   - **What happens inside that window:**
     - `v2_demo.Hw` switches the host port to pe_spim (`v2_demo.py:75`, `select(fabric=True)`);
     - it then reads the ID over it (`:78`, a `check` that raises on a mismatch);
     - `HwRig` enables quad (`postfreeze_demo.py:90`) and measures the clock for 2 s (`:94`).
   - **The result.** For those roughly 2 s, any of these leaves the host port on the fabric path with quad enabled, and nothing calls `close()`:
     - an exception, including the ID check failing, which is the likely error on a wrong bitstream;
     - Ctrl-C;
     - SIGTERM or SIGHUP, which still have their default action and kill the process.
   - **The test doesn't see it.** `test_run_closing_*` only covers what happens once `run_closing` holds a backend.
   - **How I showed it.** My `window.py` uses `test_fabric_ping`'s fake board (FakeSpimMem logs every SEL write) and runs `tap.main(["--backend", "fpga", "--probe"])` with the guard stubbed to `protoemu`:
     ```
     normal run (no fault): main returned 0; SEL writes [1, 0] (last GPIO)
     Ctrl-C during HwRig's 2 s clock measurement: main raised KeyboardInterrupt; SEL writes [1] (last fabric)
     an exception during HwRig's clock measurement: main raised OSError: a read failed; SEL writes [1] (last fabric)
     SIGHUP during HwRig's clock measurement (default action stood in by SystemExit): SEL writes [1] (last fabric)
     ```
     In the SIGHUP case, a Python handler raising SystemExit stood in for the default action. The real default kills the process, so it is the same or worse.
   - **Fix.**
     - Install the signal handlers before `make()`.
     - Have `board_rig` (or `HwRig.__init__`) give back a partly built rig on any exception.
     - Add a test that interrupts `HwRig`'s construction on the fake board.

   Or narrow the claim to "once the backend is built".

2. **`--compare` and the probe never look at the link pulse.**
   - **What is compared.** `fabric_eth.decode_td` (`:196-204`) puts any TD burst of `PULSE_MAX_NS` or less into `info["pulses"]`. `compare` checks `DETERMINISTIC` (`eth_tap.py:554`) and `tx_timing` (`:557`), which compares `tx_edges`, the edges of the one frame burst. `tx_pulses` is in neither, and the probe's `pass` does not check it either.
   - **So the doc overstates.** `docs/eth10-network.md:349` says "the board's TD edges … are one of the model's divider phases". In fact the frame's edges are compared, from its first transition to the end of TP_IDL, and the link pulse is not, nor how many pulses there are.
   - **How I showed it.**
     - I copied the board file and gave each reply two extra link pulses (`[[5,5],[2000,5],[4000,5]]`). `--compare` still prints **SAME**.
     - Moving one frame edge by one clock gives DIFFERENT, so the frame comparison itself is real.
   - **This run is unaffected.** I checked by hand: board and model have `tx_pulses == [[5, 5]]` for arp, ping and udp.
   - **Fix.** Compare `tx_pulses` too, or say "the frame's TD edges" and drop "every TD edge".

## Checked

**The board evidence** (`evidence/029/`):
- `--compare fabric-ping-b.json fabric-ping-m.json`, run myself: `arp: same`, `bad_fcs: same`, `other_mac: same`, `ping: same`, `udp: same`, `SAME`, rc 0.
  - The TD edges are "947 … divider phase 1", "939 … phase 4" and "917 … phase 0".
  - The two no-reply cases are "same" on the RX fields only, which is all they have.
- The board file:
  - `rig: KV260`, RTL `d1aa46f7ed7e`, `f_meas_mhz 49.99916`;
  - every result has `pass: true` and `received_same: true`;
  - each reply has `td_same` and `td_fcs_ok` true;
  - zero words restored are 6 (arp) and 2 (each of the others), with 974 generator entries for the ARP. These agree with the model file and with the doc's table.
- The model file has 4 distinct waveforms over the 5 phases, as the doc says. The 100 ns link pulse, the 40/60 ns half cells and the 290 ns TP_IDL at phase 0 match its numbers.
- `selftest.log`:
  - 5/5 pings, rtt avg 52.271 ms;
  - `udp_echo "wirelingo"`, all four `no_reply_*` true, `answers_again` true;
  - station `arp_ignored 1`, `bad_fcs 1`;
  - backend stats `rx_frames 13`, `tx_frames 8`, with no `rx_gap`/`rx_lost`/`rx_differs`/`tx_lost`/`tx_differs`;
  - `"pass": true`, `f_meas_mhz 49.99954` (a separate run from the probe's 49.99916).

**Zero-word restoration** (`fabric_eth.restore_rx`). What constrains the frame:
- The record's bit count, from the firmware's trailer, fixes the number of data words.
- Every non-zero word must sit on the 15-read cadence (within 2 reads) at a place strictly after the last one.
- The probe then requires the recorded frame to equal the sent frame byte for byte, including `bad_fcs` and `other_mac`.
- The selftest fails on any `rx_differs`.
- `LINESTAT`'s overflow bit is checked.

So a lost, extra or misplaced non-zero word, or a wrong length, cannot pass. The mutants `fab-zero-words-not-restored`, `fab-last-word-dropped`, `fab-no-cadence-check` and `fab-cadence-not-refined` are each killed. Note 2 covers the one case these checks cannot see.

**Guard parser.** `rows()` refuses any line that is neither the header nor a valid row. I ran `parse` on the 58-line fixture: it gives `protoemu`, with 57 rows and one active row. Each of these spoiled versions refuses:
- a `protoemu: Load Error: -1` line appended;
- empty stdout;
- a header alone;
- two headers;
- an unknown `WARNING` line;
- a row with an extra column;
- a merged row whose suffix is not its name.

`listapps()` also refuses a non-zero exit status, and `check()` adds the firmware-name, fpga_manager-state and installed-file checks. `hw_day` preflight now reads rows (`hw-preflight-substring` killed). The `install.sh` grep shows the app's row, the header and any line containing "rror". I can't confirm the fixture is verbatim from the board. It is the orchestrator's capture, and it parses as described.

**Fake KV260 test.** `test_the_boards_code_path_on_a_fake_board_agrees_with_the_model_to_the_clock`:
- runs `HwRig`, `pe_spim.Spim`, `pe_lab.Lab` and the burst's register writes on register maps that drive the model;
- asserts `compare(...) == 0` against the ModelRig probe;
- asserts every PMOD write is 0, SEL ends at 0, pe_lab ends in GPIO mode and the cores are disabled.

My `window.py` normal run reproduces this: SEL `[1, 0]`, probe PASS.

## Notes

1. **Evidence level in the docs is right for this commit, and the board run is not in the repo yet.**
   - `docs/eth10-network.md` says "Evidence level: model … Until the orchestrator reports a board run, nothing in this section has run in FPGA fabric". `:7` says "Nothing here has run on an FPGA". Neither claims more than the commit had.
   - When the board run goes in, use the level the item states: RTL in FPGA fabric (KV260), one frame at a time, 64-byte frames each way, generator into RXP and TD read inside the FPGA, no cable, no PMOD pin. Then update `:7` and the "not yet on the board" table row.
2. **What the zero-word restoration cannot see.** A blind read of an empty FIFO and a read of an all-zero data word both give 0. So whether the chip actually pushed its all-zero words, or pushed an extra one, is not observed. The words are put back from the bit count, and the content is right either way.
   - On the board this applies to 6 of the ARP request's 24 data words and 2 of each other frame's.
   - The FCS and the byte comparison "check the result" (doc) in the sense that the frame is right. They do not show those words came out of the FIFO.
   - This is inherent to the method. A sentence in the doc saying so would make the evidence exact.
3. **`--pcap` came out 0 bytes; nothing claims a pcap from the board.**
   - `--pcap` predates this branch (`97e442d`). No doc mentions it for the fpga backend.
   - The code starts tcpdump with stderr discarded and never checks its exit status (`eth_tap.py:444-484`), so a tcpdump that fails to start or to write goes unnoticed. A 0-byte file means it never wrote even the pcap header.
   - Worth reporting in the selftest result (exit status and file size). I did not try to diagnose it, since that needs the board.
4. **Stale wording.** `docs/eth10-network.md:369` says the logged runs used "`07fef6e`, whose code is the same as this commit's".
   - Since then 79cb8d9 changed `fabric_guard.py`, `hw_day.py` and `install.sh`.
   - The model runs in that table don't go through the guard, so the numbers stand. My `fab-*` run at da82a51 also covers it.
   - Say "same `fabric_eth.py` and `eth_tap.py`", or name the later commit.

## What I ran

**On runner1:**
- `remote_test.sh model` at da82a51: `model rc=0`, `2360 passed, 28 skipped in 793.48s (0:13:13)`, "SIGROK CHECKS SKIPPED: 28 tests" (checkout `agent-runs/protoemu-verify-da82a51-734819`).
- In my clone `~/verifier-029/`, `python test/mutants.py 'hw-*' 'fab-*'` with `TMPDIR` inside the clone: `94 of 94 mutants killed`, rc 0. Each of the 15 `fab-*` mutants is killed by the test of its check.
- `window.py` (finding 1), on the fake board from `test_fabric_ping`.

**Locally (reading only, no pytest):**
- `eth_tap.py --compare` on the evidence files;
- the two edited copies (finding 2);
- `fabric_guard.parse` on the fixture and its spoiled variants;
- the archive sha256.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
