# RTL-to-netlist equivalence

`formal/equiv/equiv_check.sh RUN` proves that a hardening run's final netlist
(`runs/wokwi/final/nl`, after placement, clock-tree synthesis, repair, hold
fixing, antenna repair and routing) implements the RTL the run synthesised.
It is a formal check, not a simulation: every compared point is proved for
all inputs and all states.

**Result (2026-10-14, tool commit 090fd79).** Run fo6, the release candidate
(netlist sha256 `a94a948b70591b22…`): **equivalent**. All 7429 compared points
are proved by both engines, nothing in the netlist is unmatched, and the five
planted netlist bugs are all caught. Run t48ba (`e400fa30c291f653…`), which
differs from fo6 by one buffer, is equivalent too.

## What is proved

Gold is the RTL as LibreLane read it: the nine `src/` files of the run, the
defines the synthesis step adds (`SCL_sg13cmos5l_stdcell`, `PDK_…`,
`__librelane__`, `__pnr__`, `PAD_…`) and the parameter values they give
(`NCORES=2`, `NBANKS=2`, `FIFO_LOG2=3`, `BANK_AW=8`). The run's files are
byte-identical to `src/` at main f6de125. Gate is the final netlist, with
every standard cell replaced by its PDK functional model.

The state elements correspond one to one:

| | gold (RTL) | gate (netlist) | correspondence |
|---|---|---|---|
| flops | 1652 bits | 1647 `dfrbpq_1` | 1609 by net name, 3 by FSM-bit name, 35 by pairs that simulation proposes and the proof checks; the RTL's 3 `phase` bits map to 4 one-hot flops; 6 RTL bits have no netlist flop (below) |
| latches (FIFO storage) | 1024 `dlhq_1` | 1024 | instance name |
| clock gates | 54 `lgcp_1` | 54 | instance name |
| SRAM macros | 2 | 2 | instance name |

Each correspondence is a cut point. With all of them cut, the two designs are
combinational, and these 7429 points are proved equal for every value of the
inputs and of the state:

| points | count |
|---|---|
| next state (D) of each flop | 1647 |
| clock of each flop (rising-edge form) | 1647 |
| asynchronous reset of each flop (active-low form, or constant 1) | 1647 |
| latch D and GATE | 2048 |
| clock-gate CLK and GATE | 108 |
| SRAM inputs (clock, enables, address, data, mask, BIST pins), 154 per macro | 308 |
| outputs `uo_out`, `uio_out`, `uio_oe` | 24 |

Equal next-state, clock and reset functions on corresponding state elements,
and equal outputs, make the two designs sequentially equivalent from
corresponding states: at every clock edge, reset or latch transparency they
compute the same values and stay in corresponding states. The reset values
correspond (checked), so asserting `rst_n` (and CS_n for the host port's SCK
domain) puts both in corresponding states.

Some flops correspond with inverted polarity. The flop synthesis uses
(`dfrbpq`) only resets to 0, so synthesis stores a register that resets to 1 inverted. For these 27 flops (26
RTL bits that reset to 1, and the one-hot bit of the FSM's reset state) the
netlist state is the RTL state XOR 1, and D is compared XOR 1. A flop's
polarity must equal its RTL reset value, or the check fails.

**The re-encoded FSM.** Yosys extracted the host port's `phase` register
(3 bits, 5 states) and re-encoded it one-hot. Its synthesis log gives the
table. Netlist flop *i* holds `ENC_i(phase)` XOR its polarity. The check
drives those flops from the gold `phase` and compares their next states with
`ENC_i` of the gold next state. Synthesis dropped the bit for `PH_IGNORE`:
that state has no exit except CS_n, so in the netlist it is the all-zero code
of the other four bits. The check covers every 3-bit `phase` value, not only
the five legal ones. A wrong table would make the check fail, not pass.

**RTL flops with no netlist flop** (inputs that the gold design reads and the
netlist doesn't). Every compared point is proved for all their values, so
they are unobservable, which is why synthesis removed them:

| RTL bits | why they are unobservable |
|---|---|
| `u_chip.ctrl_data[7:6]`, `[11:10]` | restart and FIFO-clear bits of cores 2 and 3, absent in the 2-core build |
| `u_host.sh[31]` | the next shift value is `{sh[30:0], in}` or `{sh[27:0], in}`; the top bit is never read |
| `u_host.cmd[7]` | the command byte is read as `{cmd[6:0], in}` |

**Undefined values.** After `proc`, the gold design has 5994 constant `x`
bits (unreachable branches of `case` defaults and partial assignments,
mostly in `pe_line` and `pe_core`). The ABC check makes each one a free
input, so it proves the netlist for every resolution of every `x`: no
compared point depends on them. equiv_simple reads them as 0, which also
passes.

## How (`formal/equiv/`)

1. **Inputs.** The run's synthesis step: its `config.json` (sources, defines,
   cell models, macros), its log (the Liberty file and FSM recodings) and its
   netlist. Every input is hashed in `inputs.json`.
2. **Cells** (`cells.py`). The PDK's `sg13cmos5l_stdcell.v` is the source of
   cell functions. Yosys can't read Verilog UDPs, so the two combinational
   ones (`ihp_mux2`, `ihp_mux4`) are translated from their truth tables. Every
   0/1 input combination must match exactly one output. The `specify` blocks
   are dropped. Cells built on sequential UDPs become port-only stubs. Every
   combinational cell type the netlist uses (48) is then proved equal to its
   Liberty `function` from the file synthesis used. The flop's Liberty `ff`
   group must read `clocked_on: CLK`, `next_state: D`, `clear: !RESET_B`, the
   semantics the check assumes for `dfrbpq`.
3. **Gold.** `read_verilog -sv` with the flow's defines, `hierarchy`, `proc`,
   `flatten`, then flops split into single bits. Beyond removing unused
   wires and cells (`opt_clean`), no optimisation runs. The
   run fails if any latch, memory or set/reset flop is inferred: the RTL's
   only latches are the library cells it instantiates.
4. **Gate.** Final netlist plus cell models, flattened. Buffers, inverters,
   clock-tree cells, delay and hold cells, SRAM pin buffers and tie cells
   become their functions. Fill, decap and antenna cells have no outputs.
   Tristate cells are not modelled, and the run stops if the netlist uses any.
5. **Match** (`cut.py`, `pair.py`). Flops are matched by the RTL name on the
   output net in the final netlist. If P&R renamed that net, the synthesis
   netlist is used, since instance names survive P&R. Flops whose names
   synthesis dropped get pairs proposed by a bit-parallel random simulation
   (`sim.py`). A gate flop and an RTL flop pair when their clock, reset and
   next state agree on 256 random patterns. Before that, a gate flop whose
   net carries the name of a gold signal is paired with the gold flop that
   signal equals: `u_line.m_e[4:0]` and `m_q[6:5]` equal `u_line.q[6:0]`, and
   `crc_mask[8]` equals `crc_w[3]`. Pairs are only proposals: the proof
   checks every one.
6. **Prove.** Two engines run on the same cut modules:
   ABC `&cec` on the whole miter, with every RTL `x` free, and yosys
   `equiv_make`/`equiv_simple` per point, in parallel shards. `equiv_status`
   lists any unproven point by name. For up to five of them, a SAT
   counterexample goes to `OUT/cex/cexN/cex.log`. It gives the value of every
   design input, flop state (`S:`) and black-box output (`O:`), and the
   differing gold and gate values. A planted D-net swap shows the format.
7. **Mutants** (`--mutants`). Five netlist bugs must each be caught: a hold
   delay cell made an inverter, a clock-tree buffer made an inverter, two
   flops' D nets swapped, two clock gates' GATE nets swapped, two SRAM
   address bits swapped. These check the reader, the cut and the matching,
   not only the engines.

`result.json` in the output directory records all of this. The exit status
is 0 only for a full proof.

## Assumptions and trust base

* **Tools.** Yosys 0.69+158 (OSS CAD Suite) elaborates the RTL. Synthesis used
  Yosys 0.66 (LibreLane 3.1.0.dev3). A front-end misreading of the RTL common
  to both versions would not show here. RTL simulation (Icarus/cocotb,
  lockstep against the model) and the FPGA build read the RTL with other
  front ends. ABC and yosys' SAT engine prove independently, but both prove
  the same cut modules. The mutants check those modules.
* **Cell models.** The PDK's Verilog functional models define the cells, and
  their combinational functions are checked against Liberty. Whether the
  cells' transistors implement those functions is the PDK's responsibility;
  this check does not cover it.
* **Zero-delay semantics.** Cells are Boolean functions. Clock-tree buffers,
  the clock delay chains (SRAM clock advance, late pin-register clocks), hold
  buffers and pin buffers are buffers by their models. The check proves each
  clock pin gets the same Boolean function (of the clock inputs, the clock
  gates' outputs and the state) as in the RTL. Delays,
  skew, glitches, setup/hold and metastability belong to STA and the SDF
  simulations ([verification.md §5](verification.md#5-gate-level)).
* **Black boxes.** The latches, clock gates and SRAMs are the same cells on
  both sides, because the RTL instantiates them under
  `SCL_sg13cmos5l_stdcell`, as in the flow. The check proves only that each
  gets the same inputs. Their internal function is not part of it. RTL
  simulation uses the behavioural branch of `src/pe_cg.sv` instead: a latch on
  `!clk` ANDed with `clk`, and a transparent-high latch. It is the same
  structure as the PDK models of `lgcp_1` and `dlhq_1` by inspection, not by
  this proof.
* **Initial state.** The equivalence holds from corresponding states. Reset
  flops get there by reset. Elements with no reset start with the same
  unknown content in both, being the same cells: 128 FIFO write-data flops,
  1024 FIFO latches, 54 clock-gate latches and the SRAM contents. One case
  has no RTL counterpart until reset: the host port's `phase` is reset by
  CS_n high. If CS_n is low from power-up, the netlist's one-hot flops can
  start in a code such as two bits set, which no RTL state maps to. The proof
  covers every state after CS_n has been high once; the host-port proof has
  the same caveat ([review.md](review.md), "power-up with CS_n low").
* **Power.** `final/nl` has no supply pins; supply connectivity is LVS's.

## Results

Runs on runner1, `-j 4`, tool commit 090fd79:

| run | netlist | ABC &cec | equiv_simple | mutants | time |
|---|---|---|---|---|---|
| fo6 | `a94a948b70591b22…` | equivalent | 7429 / 7429 | 5 / 5 caught | 8 min 48 s |
| t48ba | `e400fa30c291f653…` | equivalent | 7429 / 7429 | 5 / 5 caught | 6 min 22 s |

Step times for fo6: reading the inputs, cells, RTL and netlists 12 s,
matching 29 s, ABC 27 s, equiv_simple 393 s (4 shards), mutants 68 s. ABC
alone decides the whole design in under 30 s. The busiest process peaks at
1.6 GB. runner1 was shared with other jobs (load about 8 of 20 cores).

**Unproven points: none.** No mismatch was found, so there is no
counterexample to analyse. While the tool was being built, every failing run
traced to the harness: unmatched or wrongly matched flops (for example,
`phase[2]` matched by name before the FSM recoding was handled), never to the
netlist.

## Run it

```
formal/equiv/equiv_check.sh -j 4 --mutants ~/protoemu-flow/protoemu-exp-fo6 [OUT]
make -C formal equiv RUN=~/protoemu-flow/protoemu-exp-fo6 JOBS=4 EQUIV_FLAGS=--mutants
```

It needs python3 and OSS CAD Suite (`$OSS_CAD`, default
`~/tools/oss-cad-suite`), and the PDK paths recorded in the run. It reads the
run directory and writes only to OUT (default
`~/protoemu-flow/equiv-<run>-<UTC time>`). Processes peak at about
1.6 GB. It is not part of CI. Run it on runner1 for each candidate netlist.
`test/model/test_equiv_tools.py` covers the UDP translation, the FSM log
parser and the simulator.
