VERDICT: PASS

071 pkg3-rtl (branch `ao-pkg3`, `aeca4ad`..`10f07bf`, tip `3a7feab`, backlog AO): verifier report.
Kria: verifier does not touch it. No SWD.

The v0.3 RTL matches `docs/isa.md` and the model at every corner I read, and the v0.2 behaviour is unchanged. Each reproduction matches:
- lockstep at all three geometries;
- the top-level suite at 3 and 2 cores;
- the extra 3-core suites;
- formal and its planted bugs;
- the 16 RTL mutants;
- the synthesis areas.

In the netlist, the table read's new path into the OSR is 2–4 cells, as claim 2 says. I also reran the Lean fetch-ownership check at 3 cores on runner1. It fails exactly as the builder says, and a one-line testbench change makes it pass at 3 cores with its negative controls intact (finding 2). Finding 1 is a rule note about where the builder ran Lean. Finding 3 is a pointer for AP's harden.

## Findings

1. **Rule note: the Lean checks were run locally** (claim 6: "run locally, niced: Lean 4.28.0 is not on runner1").
   - **The rule:** RULES.md:6 puts formal on runner1 only. The Lean-backed checks are formal (`formal/lean`).
   - **Why I don't fail on it:** the builder disclosed the breach, and the check is light (about 2 minutes on runner1 for all four of my runs). A Lean 4.28.0 install is now on runner1 (`~/protoemu-flow/ao-pkg3/lean-4.28.0`, with a runner1 script dated today), and I reproduced every Lean result there (below). The artifact is unaffected.
   - The orchestrator may weigh it otherwise. For next time: run `formal/lean` on runner1.

2. **The Lean fetch-ownership testbench left at 2 cores.** The builder's reason is right, and the gap can be closed.
   - **Reproduced on runner1:**
     - At 2 cores the check passes ("result": "pass"; its planted-mutant and shifted-trace controls fail at clocks 29 and 31).
     - At 3 cores (`.NCORES(3), .NBANKS(3)` in a scratch copy) it fails at clock 0, with `differences: ['inactive_writes']` only.
   - **Cause:** `rtl_fetch_ownership_tb.sv` ORs cores 2–3's write *values* with their enables. A present but disabled core 2's value buses are X from its unloaded bank, while its enables stay 0.
   - **The fix works:** in the same scratch copy I masked the values by their enables: `(pre_en | (pre_val & pre_en) | pre_oe_en | (pre_oe_val & pre_oe_en))` over bits 127:64. That is a testbench-only change. The 3-core check then passes, with both negative controls still failing at clocks 29 and 31.
   - **Caveat:** even then the testbench observes only cores 0 and 1 by name. Bank 2's ownership by core 2 has RTL-simulation evidence only: the 3/3/8 lockstep runs core 2 from its own bank, checking every register every cycle.
   - **Suggestion:** take the masked form and the 3-core build into the testbench, and add core 2's PC, fetch and instruction to its observed fields when the Lean model allows. Until then, the testbench comment (`:14-18`) states the limit honestly.

3. **Table-read depth (claim 2): confirmed in yosys's netlist for the OSR. The logic-only STA also points at the CRC and R0–R3 endpoints, which claim 2 doesn't list.**
   - **The netlist:** I synthesized with `synth_chip.sh`'s flow (yosys + abc, IHP typ lib), then read `pe_core` (IDX 0). Submodule instances and flops break paths.
     - `instr[i]` → `osr[i].D`, shortest: **2–4 cells** at v0.3 (bit 0: `inv, a21oi, o21ai, nor2b`; bit 31: `a22oi, a21oi`). At v0.2 it was 4–13, and missing for 13 bits. This matches "about three gate levels".
     - The longest `instr` → OSR path is **27 cells** at v0.3, against 29 at v0.2. The long decode/exec path keeps its depth. ✓
     - **`instr` loads.** The total is 388 at v0.3, against 401 at v0.2, because abc restructures the decode. Per bit, the changes run from −5 to +8 cell inputs, with bits 19–31 up by 0 to +8. So "one more load on each DOUT bit" is the RTL structure, not a count in the mapped netlist.
     - **The PC's D input:** 29 → 31 cells, consistent with the `tbl_issue ? pc_seq : fetch_addr` mux after `fetch_addr`. `fetch_addr` itself is 29 → 29, so nothing was added on the SRAM address. ✓
   - **Logic-only STA.** OpenSTA in the LibreLane image on the flattened yosys netlists, at the slow corner. Clocks are ideal and propagated, with no wires and no buffering, and the period is 20 ns. **These are not timing claims**, only relative signals:
     - **Mapping noise is large:** the 2-core and 3-core v0.3 netlists, with the same core RTL, differ by 1.9 ns at their worst endpoint.
     - **The OSR keeps its place.** Worst OSR slack relative to the worst pin endpoint: v0.2 +1.35 ns; v0.3 +1.44 (2 cores) and +1.30 (3 cores). That fits claim 2.
     - **The CRC register and R0–R3 fall behind the pins.** In v0.2 the worst endpoints are `pin_oe`/`pin_out`, with CRC 0.48 ns and R0 0.59 ns better. In both v0.3 netlists the worst endpoint class is `crc`, followed by R0–R3: CRC is 0.69–0.78 ns and R0 0.45–0.56 ns worse than the pins. That is about 1.1–1.2 ns relative.
     - **Likely causes:** CMPW's immediate mux in front of the ALU, which claim 2 names, and the any-width CRC (`crc[crc_w]` select and 32-bit mask), which claim 2 doesn't name.
   - **For AP's first 8x4 harden of this RTL:** report the worst slow path per endpoint class (pins, CRC, R0–R3, PC, OSR), not only the overall worst, so this pointer is confirmed or dropped. The builder's own caveat ("only a harden measures the slow corner") stands.

## Asked checks

- **`pe_core.sv` against §4.1, §4.5, §5.2, §5.3 and the model.** I read the full RTL diff.
  - **Table read.**
    - `mop == 3` sets `jump` with target `{pc[9:8], mov_val[7:0]}`, so `fetch_addr` presents the table address.
    - `tbl_issue = commit && MOV && mop == 3 && !host_pc_w`, and `pc <= tbl_issue ? pc_seq : fetch_addr`.
    - `tbl_pend <= tbl_issue`, and `exec` includes `!tbl_pend`, so the read's clock never executes.
    - `if (tbl_pend) osr <= instr; osr_cnt <= 0` comes first in the sequential block. Only the restart branch overrides it, because the exec branch is under `!tbl_pend`. In the active branch, `tbl_pend` is the first case under `tick && !halted && !fault`, so there is no delay countdown and no bank-fault check that clock, as in the model.
    - The write is outside `active`, so it happens while disabled.
    - A host PC write in the MOV's clock clears `tbl_issue` and wins `fetch_addr`. ✓
  - **Tick counter.** `tcnt` increments when `active && tick`, also when halted or faulted, which matches §4.1. It is cleared on restart and holds while disabled. The source reads `tcnt` before the increment. ✓
  - **CMPW.**
    - `alu_imm = {is_cmpw ? x[17:16] : 2'b0, x[13] && is_cmpw, x[12:0]}`. That is `{x[17:16], x[13:0]}` for CMPW, and `zext(imm13)` otherwise, as before.
    - `aop` is forced to 7 (cmp), and `alu_a` takes rd from `[15:14]`.
    - Only `z_we` is set, so Z and C come from the cmp result and no register is written. ✓
  - **The host CRC write rule.** `crc <= {crc_w[4] ? wdata[31:16] : 0, wdata[15:0]}`, that is, 32 bits exactly when W > 16, as in the model and §6.2.
    - CRCCTRL is `{poly, crc_w (5 bits: [23], [19:16]), in_tap, out_tap}`.
    - POLY_HI is an enable flop at offset 0x11, write-only.
    - `crc_mask = 0xFFFFFFFF >> (31 - crc_w)`, and the feedback is `crc[crc_w]`. ✓
  - **`!t`, `flag tog` and bank-relative OUT/MOV PC** are as in the spec.
  - **ID:** `{16'h5045, NBANKS, NCORES, 8'h03}`.
  - **Resets:** every new flop resets to 0 (`crc` 32 bits, `tcnt`, `tbl_pend`, `crc_poly_hi`, the 23-bit CRCCTRL group), and the existing reset values are unchanged.
  - **The properties** in `pe_core.sv`'s formal block match claim 6.
- **Lockstep** on runner1, my clone at `3a7feab`, OSS CAD Suite Icarus + cocotb. The RTL is unchanged after `8c4c635`.
  - **3/3/8** (`LOCKSTEP_SEEDS=40 LOCKSTEP_CYCLES=1000 PE_NCORES=3 PE_NBANKS=3 PE_FIFO_LOG2=3 make TB=chip`): "TESTS=7 PASS=7 FAIL=0".
  - **2/2/8** (40 × 1000): "TESTS=7 PASS=7 FAIL=0".
  - **4-core flop build** (`LOCKSTEP_GOALS=core LOCKSTEP_SEEDS=30 LOCKSTEP_CYCLES=1000 make TB=chip IMEM=flop`): "TESTS=7 PASS=7 FAIL=0".
  - **v0.3 coverage in the random runs** (two of the logs):
    - `table:read` 72 / 101, `table:extra_tick` 56 / 78, `table:restart_wins` 1, `table:write_while_disabled` 1;
    - `cmpw:lt` 44 / 97, `cmpw:gt` 8 / 18, `cmpw:eq` 2;
    - `jmp:not_t_taken` 87 / 136, `jmp:not_t_fallthrough` 4 / 5;
    - `flag:tog_set` 34 / 50, `flag:tog_clear` 28 / 36;
    - `ticks:read` 4896 / 5105.

    The host-PC cancel is not reached by the random stimulus. It is exercised by `test_v03_corners`, which kills `rtl-table-not-cancelled-by-host` (below).
- **Top-level.**
  - `make` (3 cores, the new default): "TESTS=30 PASS=30 FAIL=0".
  - `make PE_NCORES=2 PE_NBANKS=2`: "TESTS=30 PASS=30 FAIL=0".
  - Both include 072's `test_eth10_fcs_in_engine_loopback` and `test_usb_autonomous_ep1_fs`, which PASS.
  - At 3 cores: `test_pio` "TESTS=2 PASS=2", `test_eth_service` "TESTS=2 PASS=2", `test_post_freeze` "TESTS=16 PASS=16 FAIL=0".
- **Formal.** `make -C formal` (runner1, 26 min): 9 targets all "DONE (PASS, rc=0)": `fifos_cover8`, `fifos_prove4`, `fifos_prove8`, `hostport_prove`, `hostport_cover`, `hostport_ref_prove`, `hostport_ref_cover`, `core_prove` (with the new table-read, frozen-state and tick-counter properties) and `line_prove`; "make-formal rc=0". `formal/core_mutants.sh`: `table-word-inverted`, `table-clock-executes`, `table-pc-jumps`, `table-word-dropped`, `ticks-plus-2` and `ticks-kept-on-restart` each end "DONE (FAIL" (6 of 6), "core_mutants rc=0". I did not try the swapped-out "counts every active clock" mutant; the script says why it is out.
- **Mutants.** `test/mutants.py` at `3a7feab` on runner1. Every baseline passed, and a kill needs an executed test's failure. All 16 of 16 are killed:
  - `rtl-cmpw-as-ldi`, `rtl-cmpw-imm-top-bits`;
  - `rtl-crc-host-write-32`, `rtl-crc-poly-hi-ignored`, `rtl-crc-width-4-bits`;
  - `rtl-not-t-always`, `rtl-pc-write-absolute`;
  - `rtl-table-no-extra-tick`, `rtl-table-not-cancelled-by-host`, `rtl-table-pc-jumps`, `rtl-table-word-executes`, `rtl-table-word-not-written`;
  - `rtl-ticks-kept-on-restart`, `rtl-ticks-read-divider`, `rtl-tog-as-set-clear`, each by `test_v03_corners`;
  - `rtl-id-v02` by `test_registers`.
- **Synthesis.** `syn/8x4-study/synth_chip.sh` on runner1. All of it matches claim 3:

  | build | cells | flops | latches | area (µm²) |
  |---|---|---|---|---|
  | `3a7feab`, 3 cores | 29,698 | 2229 | 1615 | 423,427 |
  | `3a7feab`, 2 cores | 21,379 | 1747 | 1078 | 308,288 |
  | `d2ed344`, 2 cores | 20,155 | 1647 | 1078 | 295,218 |

  - `pe_core[0]` is 73,070 at v0.2 and 80,357 at v0.3 (+7.3K, +10 %).
  - The 2-core estimate recomputes: 308,288 × 1.2764 / 1,109,193 µm², the stdcell area available in 069's base harden, is 35.5 %. The 51.0 % for 3 cores relies on the study's SRAM-outline deduction, which I didn't rederive.
- **Depth.** Finding 3.
- **Lean** (runner1, a copy of the Lean 4.28.0 install there, OSS CAD Suite Icarus):

  | run | result |
  |---|---|
  | `run_fetch_ownership.py`, testbench as committed (2 cores) | rc 0, "pass" |
  | `check_rtl_rx_autopush.py --phase all` (3 cores, as committed) | rc 0, "pass" |
  | fetch ownership at 3 cores | rc 1, fails at clock 0 on `inactive_writes` |
  | fetch ownership at 3 cores, masked | rc 0, "pass" |

- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `3a7feab`: 727 files, 14 messages, "TOTAL flagged: 0".
  - My private-string check: "TOTAL 0".
  - All commits are by the noreply identity and carry the trailer.
  - `b2fa7f3` is not an ancestor.
  - In `src/` only `pe_chip.sv`, `pe_core.sv` and `project.v` changed. `flow/`, `info.yaml` and `docs/methodology.md` are untouched.
- **ISA timing check.** The table read takes 2 + delay ticks at CLKDIV 1, consistent in the spec, model, RTL (`tbl_pend` spends the tick) and checker. `rtl-table-no-extra-tick` and `model-table-no-extra-tick` are killed.

## What I ran

- **runner1**, all under `~/protoemu-flow/verifier-070/`: one clone of `ao-pkg3` with a worktree per job.
  - the three lockstep geometries, the top-level suite at 3 and 2 cores, and `test_pio`, `test_eth_service` and `test_post_freeze` at 3 cores;
  - `make -C formal` and `formal/core_mutants.sh`;
  - `test/mutants.py`;
  - `synth_chip.sh` 3 and 2 on the tip, and 2 on `d2ed344`;
  - my netlist JSON and depth reads (`depth2.py`), and logic-only OpenSTA on the flattened netlists;
  - the four Lean runs.

  **No LibreLane harden.** AP holds the harden queue, and its gate-level runs were going on beside mine.
- **Locally:** reading the RTL, model and testbench diffs. No simulation or formal.

Summary: the v0.3 RTL is the spec, and lockstep, the top-level suite, formal, mutants and synthesis all reproduce. Close the 3-core Lean gap with the masked testbench (finding 2). AP's harden should report per-class slow paths to settle finding 3's CRC and R0–R3 pointer.
