# 069 8x4-study

Branch `study-8x4`, made from the public base (`refs/remotes/public/main`,
`aa958a7`); `git merge-base --is-ancestor b2fa7f3 study-8x4` fails, as it
must. Commits, in order, on `aa958a7`:
`a91f85c` (harden wrapper), `57947e3` (synthesis prototypes and results),
`55bdfc0` (STA script fix), `6f8242f` (any-width CRC, package rows, PROTO
overlay), `9fd9362` (budget.py, a result label), `f29c399` (fallback row),
`65d0e8e` (`docs/8x4-study.md`). 11 files, +2639. New files only: `docs/8x4-study.md`
and `syn/8x4-study/` (harden wrapper, synthesis and STA scripts, prototype
RTL, `results.txt`). No change to `src/`, `flow/`, `firmware/` or
`docs/methodology.md`.

Backlog item AN. A study, not the change: it recommends what to add with
the 8x4 area, and the author picks before any RTL lands. Evidence levels:
harden, global route, synth (yosys, chip level) and labelled estimates.
Nothing ran on the KV260; no push, no GitHub.

**Where the runs are.** runner1, `~/protoemu-flow/study-8x4-runs/`: run
worktrees `protoemu-exp-{base,grt3,grt4f2,grt4f2d58,grt3pkg,pkg3,baselc8}`
(each has `exp.log`, `result.txt`, `runs/wokwi/`), the queue scripts and
logs. The clone they ran from is `~/protoemu-flow/study-8x4-repo` (commit
`a91f85c` for base, `6f8242f` for the rest). Synthesis scratch:
`~/protoemu-flow/study-8x4-syn/`.

The builder's claims:

1. **Baseline (harden, run `base`).** The public main RTL (`src/`
   sha256-identical to fo6's run directory) with fo6's `gen_config.py`
   defaults on Tiny Tapeout's 8x4 tile (1724.16 × 710.64 µm, tt-support-tools
   `d66cf17`), banks in the bottom corners. Only the run worktree's
   `info.yaml` and generated `src/config.json` differ
   (`syn/8x4-study/harden8x4.sh`). 43 minutes.
   - 0 routing DRC, 0 KLayout DRC, LVS clean, 0 antenna, 0 slew/cap
     violations at every corner; `flow/signoff.py` PASSED.
   - Standard-cell utilization 34.1 % (fo6 46.9 %); 378,074 µm².
   - Metal3 at the global-routing step 45.7 % (142.7K / 312.4K), 0
     overflows (fo6: 63.9 %, 12).
   - Setup at 20 ns: typ and fast met; **slow −0.643 ns, 90 endpoints, TNS
     −33.8 ns**. Hold +0.158 / +0.362 / +0.059 ns.
   - Netlist sha256 `982444c9e60151fc…`, GDS `2d398ea6d90d1fa5…`.
2. **Other clocks (STA on base's netlist and SPEF, `sta_period.sh`).**
   48 MHz: slow +0.190 ns, all corners met. 60 MHz: typ and fast met, slow
   −3.976 ns (509 endpoints). The script reproduces fo6's signoff at 20 ns
   (hold +0.191 / +0.362 / +0.095, slow worst flop +0.010).
3. **The worst slow path** is bank 0's `A_DOUT[3]` → decode → pin rotators →
   pin-owner mux → `pin_out_cur[20]`, 58 cells with buffers, the same class
   physical.md describes. The area relieves routing, not this path.
4. **One settings step (harden, run `baselc8`: `--late-clock 8`, the change
   worth +0.27 ns between t48m and t48r at 6x4).** signoff.py PASSED; slow
   setup −0.624 ns (80 endpoints, TNS −16.3), hold +0.148 / +0.343 /
   +0.047 ns, no slew/cap; 45 minutes; netlist `73222029deedd20a…`. So
   +0.02 ns: 50 MHz at the slow corner on 8x4 is not a given.
5. **Synthesis (`run_all.sh` → `results.txt`, `budget.py`).** 2 → 3 → 4
   cores: 295,218 → 400,690 → 509,008 µm²; the bundle +1.9K, any-width CRC
   +8.7K, tick counter +2.6K, second line unit +42.8K, 16×16 flop RAM
   +38.6K, multi-bit tap +17.4K (all for 2 cores). Control rows with the
   prototype files and nothing enabled are within 0.2K of the 2-core row.
   LibreLane's own synthesis of the `grt3pkg` prototype is 463,542 against
   this script's 464,259. Utilization figures are estimates (× fo6's
   1.2764 hardened/synth ratio, which predicted base within 0.3 %).
6. **Routability (global route).** `grt3` (3 cores): Metal3 65.4 %, 46
   overflows. `grt3pkg` (3 cores + full ISA set + second line unit):
   73.3 %, 518. `grt4f2` (4 cores, 4-deep FIFOs): detailed placement
   failed at fo6's density; `grt4f2d58` (density 58): global routing did not
   finish in 107 minutes (1001 extra iterations, then clock NDRs dropped);
   stopped by the builder.
7. **The recommended package, hardened once (run `pkg3`, prototype RTL:
   3 cores + bundle + any-width CRC + tick counter; `PROTO` overlay in the
   run worktree only).** Global routing: Metal3 68.2 %, 31 overflows. Routes
   clean: 0 routing DRC, 0 KLayout DRC, LVS clean, 0 antenna; stdcell
   utilization 51.1 % (estimate 50.7 %). **Signoff FAILED** with fo6's
   settings: slow setup −1.571 ns (111 endpoints), hold typ −0.188 /
   fast −0.318 ns (all at bank 0's SRAM inputs), slew 7 / 22 / 2. 48 MHz slow
   −0.738, 60 MHz slow −4.904 (STA). 65 clock nets lost their NDRs in
   routing; 3 h 06 min. Netlist `d35b190ce12de505…`.
8. **512×32 macro**: clock to `A_DOUT` 6.38–6.69 ns at slow against 5.13–5.44
   for the 256×32 (liberty `cell_rise` tables).
9. **Recommendation**: a third core + any-width CRC + the v3 bundle + a tick
   counter; fallback: the same additions on two cores; release clock 50 MHz
   if the search reaches it, else 48 MHz (base meets 48 at every corner;
   then 10BASE-T at 40 MHz tolerates ±12 ns, not 802.3's ±13.5); gate on
   Dec 6 (3-core netlist through `release_check.py` at 48 MHz or better).
   Out: 4th core, second line unit (its USB + 10BASE-T case needs 60 MHz),
   512-word banks, data RAM, multi-bit tap.
10. **Flagged for the orchestrator:** implementing any 8x4 option needs
    edits to existing `flow/` files (`gen_config.py` hardcodes the 6x4 core
    corner; `fpga_profile.py`'s clock-gate conversion), which RULES.md
    forbids today; and `fpga/kv260/kv260_top.v` cannot make a 60 MHz build
    as is (`CLK_DIV / 8` truncates).
11. **Tests.** Model suite at `65d0e8e` (the tip) on runner1: 2916 passed,
    29 skipped, the same as the base (also at `f29c399`). The doc-link test
    passes with the new page. `record_check.py --repo . --tree HEAD --commits
    refs/remotes/public/main..HEAD`: 0 flagged.

The builder's caveats:
- One placement per configuration; physical.md shows ±0.4 ns of slow setup
  between placements.
- The prototypes are unverified RTL; the package harden measures something
  like the change, not the change.
- The flexibility arguments come from the record (post-freeze limitations,
  the v3 study's model work), not new tests.
- grt4f2d58 was stopped, not finished.

Asked checks:
- **Rerun the baseline 8x4 harden** from this branch (`harden8x4.sh` at
  `a91f85c` or later; NAME of your choice) and compare: utilization, Metal3
  and overflow at the global-routing step, setup and hold WS at the three
  corners, violator count, signoff result, netlist hash (fo7/ci7 reproduced
  fo6's netlist exactly, so the same is expected). Then `sta_period.sh` at
  20.833 and 16.667 ns.
- **Spot-check the synthesis areas**: `synth_chip.sh 2`, `3`, `4`, and three
  candidate rows of your choice against `results.txt`; read
  `pe_core_x.sv` / `pe_chip_x.sv` for whether each `V8_*` / `X_*` block is
  what the doc says (flop counts: TICK +16, XCH +64, DRAM +256, CRCW +33 per
  core).
- **Check `harden8x4.sh` edits only the run worktree** (no committed
  `flow/` file changes), and that `--layout spread` with 2 banks gives the
  `corners-swap` positions.
- **Read the doc's claims against the record**: the post-freeze item
  numbers, the v3 study's figures, the CI 4-core lockstep, the FAQ points,
  and every number against its log on runner1.
- **Judge the recommendation**: is a third core worth its layout risk given
  grt3's 46 overflows, pkg3's untuned signoff failure, its 3-hour runs and
  baselc8's +0.02 ns; is the 48 MHz fallback clock stated correctly; is the
  second line unit's clock argument right (LINEDIV's integer RX period,
  isa.md §4.7).
- **Privacy**: `record_check.py` as above, and a grep of the new files and
  messages.

The usual rules: `--ignore=test/model/test_swd.py` on any model run; at most
one harden at a time on runner1; no Kria.
