# 071 pkg3-rtl

Branch `ao-pkg3` (from `next-8x4` = public/main `d2ed344`; `b2fa7f3` is not
an ancestor). This item is the RTL and its verification, on top of 070's
three commits:

- `aeca4ad` src: the v0.3 core (docs/isa.md) and ID;
- `fdb0271` 3 cores by default: project.v, test/Makefile, the test workflow;
- `d5d8af0` test, formal: v0.3 lockstep, mutants, the ID, Lean testbenches;
- `1761bc3` formal/core_mutants.sh; docs/info.md (the ID), docs/hardcaml-port.md
  (the port is v0.2 only);
- `817c953` (with 072) test.py's two v0.3 firmware tests; the ownership Lean
  testbench back at 2 cores (claim 6);
- `8c4c635` lockstep: v0.3 random forms from their own stream; zeroed banks
  in test_v03_corners (claim 4);
- `b7d55e9` docs/isa.md: the table read's clock at INT 1 with a fraction;
- `fd44cb3` formal/core_mutants.sh: one mutant swapped (claim 6);
- `10f07bf` test.py: pin_owner_writes with the build's profile (claim 5).

The branch tip is `3a7feab` (072's host-model fix in tools/pe/usbhost.py; no RTL change).

Backlog item AO. Evidence levels: **RTL simulation** (cocotb, Icarus),
**formal** (SymbiYosys on pe_core; the Lean-backed finite checks), **synth**
(yosys + abc, `syn/8x4-study/synth_chip.sh`). No harden, no gate level, no
FPGA. Only `src/pe_core.sv`, `src/pe_chip.sv` and `src/project.v` changed in
src/; `flow/`, `info.yaml` and `src/config.json` are untouched (AP's).

The builder's claims:

1. **RTL = docs/isa.md v0.3** (070's spec), in `pe_core.sv`:
   CMPW (`is_cmpw` forces aop 7 and the split immediate; rd stays at
   `[15:14]`), `!t` (cond 0, sel 1), `flag tog` (FLAG [17]), bank-relative
   MOV/OUT PC, the tick counter `tcnt` (source 15), the 32-bit any-width CRC
   (`crc_w` 5 bits from CRCCTRL {[23],[19:16]}, `crc_poly_hi` at 0x11 as an
   enable flop, host CRC writes keep v0.2's 16 bits unless W > 16), and the
   table read (`tbl_issue`/`tbl_pend`). `pe_chip.sv`: ID =
   `{16'h5045, NBANKS, NCORES, 8'h03}`. Reset values: every new flop resets
   to 0; every existing reset value is unchanged.
2. **The table read's depth on the SRAM output (RTL structure, not
   measured by STA).** The word goes `instr` (the bank's DOUT) → a 2:1 mux
   selected by the `tbl_pend` flop → the OSR's hold input, then the existing
   exec and restart muxes → the OSR flop. It is the first assignment in the
   always block, so the long decode/exec path into the OSR keeps its depth
   and the DOUT path is about three gate levels long. What it adds on the
   critical paths: one more load on each of the 32 DOUT bits, and `exec`
   gains an AND input from a flop. The address goes out on MOV PC's existing
   jump path (bank-relative, so `{pc[9:8], mov_val[7:0]}`), and the PC
   register gets a 2:1 mux (`tbl_issue ? pc_seq : fetch_addr`) after
   `fetch_addr`, not on the SRAM address. CMPW adds a mux level in front of
   the ALU's second operand and on aop; `!t` one gate in cond 0. Only a
   harden measures the slow corner.
3. **Synthesis (synth, `synth_chip.sh` on runner1, this branch's RTL, no
   defines):** 3 cores 423,427 µm², 2229 flops; 2 cores 308,288 µm², 1747
   flops; next-8x4's RTL (2 cores) 295,218 µm², 1647 flops (reproduces the
   study). pe_core 73.1K → 80.4K µm² (+7.3K, +10 %). The study's prototype
   rows were 420,712 (3 cores) and 308,777 (2). Utilization **estimate**
   (× 1.2764, over the 8x4 core less one SRAM outline per bank): 51.0 %
   (3 cores), 35.5 % (2).
4. **Lockstep (RTL vs model, every architectural register every cycle,
   including the tick counter, the table-read flag and POLY_HI):**
   at `8c4c635` (the RTL is HEAD's; later commits touch test.py, docs and
   the formal script only), runner1, Icarus + cocotb of OSS CAD Suite:
   - **3 cores, 3 SRAM banks, 8-deep FIFOs** (40 seeds × 1000 cycles):
     TESTS=7 PASS=7 (test_registers, test_random_lockstep with every coverage
     goal, test_fifo_races, test_line_rx_jitter, test_wait_diff,
     test_call_ret, test_v03_corners);
   - **2 cores, 2 SRAM banks** (the fallback, 40 × 1000): 7 PASS;
   - **4-core flop build** (`LOCKSTEP_GOALS=core`, 30 × 1000): 7 PASS.
   v0.3 coverage in the 2-core random run alone: table:read 72, extra tick
   56, restart_wins 1, cmpw lt/gt 44/8, `!t` taken/fall-through 87/4,
   tog set/clear 34/28, ticks:read 4896; test_v03_corners reaches every
   table-read corner (≥ 5 each on the model replay of its stimulus).
   The first run of each found no state mismatch in 40 random seeds but
   failed on coverage (line:in_frame_end, cmpw:eq) and test_v03_corners
   failed on an OSR value: a harness bug, not a model/RTL disagreement (the
   SRAM kept the previous test's words past the program, where a table read
   reached after a restart; the fresh model had zeros). Fixed in `8c4c635`.
5. **Top-level suite through the SPI port** (test.py, SRAM model):
   at `10f07bf` (HEAD), runner1: **3 cores: TESTS=30 PASS=30**; **2 cores
   (`PE_NCORES=2 PE_NBANKS=2`): 30 PASS** (both include 072's two firmware
   tests). Also at 3 cores: test_pio, test_eth_service and test_post_freeze
   all pass (rc 0; post_freeze 16 of 16). The first 3-core top-level run (at
   `aebb2d6`) failed test_spi_master and test_spi_master_fast on a test-side
   limit: `host.pin_owner_writes` with the default 2-core profile refused
   core 2; fixed in `10f07bf` (every call passes the build's PROFILE). The
   four host suites pinned to 2 cores were not run here (CI runs them at 2).
6. **Formal.** `make -C formal core` (k-induction, depth 6): PASS with the
   new properties (the table read's clock never executes; the word lands in
   the OSR one clock later unless a restart; the PC moved on; the
   frozen-state assertion allows only the read's late OSR write; the tick
   counter counts the ticks of an active core). `formal/core_mutants.sh`:
   six planted bugs (table word inverted, dropped; the read's clock
   executing; its PC jumping; the counter +2; not cleared by restart): all
   six end DONE (FAIL) (runner1, `8c4c635`'s tree with `fd44cb3`'s script).
   A seventh, the counter counting every active clock, ended UNKNOWN
   (neither refuted nor proved at depth 6) and was swapped out; said in the
   script. Lean-backed checks (`formal/lean`; first run locally,
   then, per the coordinator, rerun on runner1 at `3a7feab` with Lean 4.28.0
   installed under `~/protoemu-flow/ao-pkg3/lean-4.28.0`, outputs in
   `~/protoemu-flow/ao-pkg3/lean-out/`, script `leanrun_r1.sh` beside them;
   same results both times): RX autopush at **3 cores** passes
   (`rc 0`); fetch ownership **fails at 3 cores** at clock 0 on its
   `inactive_writes` field only (it ORs cores 2-3's write enables and
   *values*; a present but disabled core 2's value buses are X from its
   unloaded bank), so its testbench stays at 2 cores, where it passes.
   (runner1: ownership at 2 cores `"result": "pass"`; RX autopush at 3
   cores `"pass"`; ownership with the testbench edited to 3 cores, in a
   scratch copy: first mismatch at clock 0, `inactive_writes` only.)
7. **Mutants** (test/mutants.py at `8c4c635`, runner1): the 16 v0.3 RTL
   mutants (`rtl-cmpw-*`, `rtl-not-t-always`, `rtl-table-*` ×6,
   `rtl-tog-as-set-clear`, `rtl-pc-write-absolute`, `rtl-ticks-*` ×2,
   `rtl-crc-*` ×3 against test_v03_corners on the 3-core SRAM build;
   `rtl-id-v02` against test_registers): **16 of 16 killed**. (A first run
   under the test venv's cocotb failed its baselines on a Python embedding
   error, an environment problem; rerun with OSS CAD Suite's cocotb.)
8. **CI** (`.github/workflows/test.yaml`): top-level at 3 cores and at the
   2-core fallback; lockstep 3/3/8, 2/2/8 and the 4-core flop build; the four
   host-transport suites pinned to the 2-core profile run with
   `PE_NCORES=2 PE_NBANKS=2`. Not run on GitHub (builders don't push).
9. **Model suite**: 2960 passed, 29 skipped at `d5d8af0`'s tree (070);
   **3000 passed, 29 skipped** at `8c4c635` (with 072's firmware tests).

The builder's caveats:
- `project.v`'s default is now 3 cores: a harden of this branch needs AP's
  3-bank `src/config.json`. `flow/gl_module_test.py` takes the core count
  from project.v; the release GL modules (test, test_pio, test_eth_service)
  follow it, but the four host suites pinned to 2 cores must be run with
  `PE_NCORES=2` if anyone runs them on a 3-core netlist (AQ).
- `fpga/kv260/sim/test_kv260.py`, `pe_kria.py` and `bringup_kria.py`
  still expect ID `0x50450002` (board-side, AR). `flow/fpga_profile.py`
  needs no change: POLY_HI is an enable flop, the seven gated
  registers are as before.
- The OCaml port is not extended; docs/hardcaml-port.md now says so.
- STA/timing: only claim 2's structure; nothing is measured.

Asked checks:
- **Read `pe_core.sv`** against docs/isa.md §4.1, §4.5, §5.2, §5.3 and the
  model: the table read's corners (restart in the next clock, host PC write
  in the MOV's clock, disable in the next clock), the tick counter on halted
  and disabled cores, CMPW's immediate, the host CRC write rule.
- **Lockstep** at 3/3/8 and 2/2/8 (40 seeds) and the 4-core flop build;
  `remote_test.sh lockstep` covers 2/2/8 only, 3/3/8 is
  `LOCKSTEP_SEEDS=40 LOCKSTEP_CYCLES=1000 PE_NCORES=3 PE_NBANKS=3 PE_FIFO_LOG2=3 make TB=chip`.
- **Top-level** `make` (3 cores) and `make PE_NCORES=2 PE_NBANKS=2`.
- **Formal**: `make -C formal` (all; expect ≥ 9 PASS as in CI) and
  `formal/core_mutants.sh` (6 FAIL).
- **Mutants**: the v0.3 set (names above), all killed.
- **Synthesis**: `syn/8x4-study/synth_chip.sh 3` and `2` on the branch.
- **Depth**: does claim 2 describe what yosys builds (e.g. `ltp` or a
  netlist read of the OSR's D cone from `instr`)?
- **Privacy**: `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD`.
