# Host-port formal proof: ordering, read epochs, reset

[`formal/hostport_ref.sv`](../formal/hostport_ref.sv) with
[`formal/hostport_ref.sby`](../formal/hostport_ref.sby) checks
`src/pe_hostport.sv`, read unmodified (with `-formal`, which adds its
`f_outstanding` port and its own two assertions), against a reference model of
the §6.1 serial protocol ([isa.md](isa.md#61-host-port-spi-1-bit-or-quad)).
The older [`hostport.sby`](../formal/hostport.sby) remains: it proves bus
operation and outstanding-count safety with SCK, CS_n and the clocks fully
unconstrained, from an initial reset only. This proof adds data, address and
order, under the host timing contract, with reset reasserted at any step.

## Model

* **Time.** One solver step is a quarter of a clk period. The harness
  generates clk (period 4 steps, free initial phase). SCK, CS_n, IO[3:0],
  QUAD_EN, rst_n and `bus_rdata` are free inputs. Two edges at least half a
  clk apart in real time land at least 2 steps apart, so every real-time
  ordering of edges that meets the contract has a counterpart on the grid.
  Edges in the same step are seen with the old value by every flop
  (SymbiYosys multiclock). Metastability is not modelled; P6 checks the
  crossing margins instead.
* **Reset.** `rst_n` is the chip's synchronised reset (`rstn` in
  `project.v`): it may assert at any step, and is released only one step after
  a clk rise, as the 2-flop synchroniser does. A SCK edge in the step that
  releases reset still sees reset (the yosys model of an async reset).
* **Reference model.** The harness decodes each transaction from the pins
  with its own bookkeeping: bits received per field (command, address,
  dummy, data), the §6.1 command table, address and data shifted in from IO3
  (1-bit) or IO[3:0] (quad, IO3 the MSB), read data as the chip drove IO3 or
  IO[3:0] just before each SCK rise. It doesn't use the RTL's phase or counter
  logic. A transaction is *counted* if CS_n fell with reset released and no
  reset has happened since; only counted transactions may cause bus
  operations. From the pins it keeps the bus obligations: the write word not
  yet written (address and data), and for a read the next snapshot address,
  the next commit address, the data each snapshot captured (`bus_rdata` in
  the cycle of the snapshot), and how many words the host has clocked out.
  Reset clears all obligations.

## Host contract (assumptions)

| | Assumption | Source |
|---|---|---|
| A1 | The chip starts in reset with CS_n high. | power-up |
| A2 | Reset asserts at any step; it is released one step after a clk rise. | `project.v` reset synchroniser |
| A3 | SCK high and low each ≥ 0.5 clk (period ≥ 1 clk). | §6.1 timing table |
| A4 | SCK is low while CS_n is high. | SPI mode 0 |
| A5 | CS_n low to the first SCK rise ≥ 1 clk; last SCK fall to CS_n high ≥ 1 clk; CS_n high between transactions ≥ 4 clk. | §6.1 timing table |
| A6 | After reset release, CS_n stays high ≥ 5 clk before the next transaction, unless CS_n was held low through the release (that transaction is ignored; the next one keeps the A5 gap). | §6.1 timing table and reset text |
| A7 | IO[3:0] is stable across each SCK rise (the host changes it on falls). | §6.1 |
| A8 | QUAD_EN changes only while CS_n is high, or falls under reset (HOSTCFG's reset). | §6.1 |

Nothing else is assumed: reset may hit any phase of any transaction, with SCK
running or stopped, CS_n high or low; SCK may pause anywhere; transactions
may stop mid-word; `bus_rdata` is arbitrary every cycle.

## Properties (asserted, proved by k-induction)

LW = LC = LX = 16 steps = 4 clk.

* **P1 writes.** Each complete data word of a counted WRITE (0x02/0x42) is
  written to addr + n, and of a WRITE_FIXED (0x0A/0x4A) to addr, with the
  bits the host sent, in order, exactly once, within LW of its last bit.
  Every `bus_we` must match such a word; there are no others. At most one
  written word is pending at a time.
* **P2 reads.** The n-th snapshot of a counted READ (0x03/0x43) is of
  addr + n, of a READ_FIXED (0x0B/0x4B) of addr. Each word the host clocks
  out (every bit, MISO or quad IO) equals the data the snapshot with the same
  index captured, and that snapshot was taken in this transaction. The n-th
  commit is of the n-th word, only after the host has clocked out its last
  bit, within LC of it. At most two words are snapped and not committed.
* **P3 read epochs.** A counted read is cancelled exactly once, after CS_n
  rises, after all its complete words are committed, within LX of the CS_n
  rise. No snapshot, commit or cancel happens outside a counted read's epoch.
  Nothing of a transaction (write, commit, cancel) is pending when CS_n falls
  for the next one, or when its address arrives.
* **P4 partial words** (follows from P1 to P3): a word cut short by CS_n
  rising is neither written nor committed; the cancel drops the words fetched
  ahead.
* **P5 reset.** After reset asserts at any step, no bus operation happens
  until a counted transaction (legally re-armed by A5/A6) asks for it: no
  write of a word in flight, no snapshot data from before the reset in a
  later read, no commit or cancel of the aborted read. Counted transactions
  after reset meet P1 to P3, including their latency bounds, so the port is
  ready again. The quad drivers (`io_oe`) are on only in a counted quad
  read's data phase and are on whenever the host samples there; at most one
  bus operation per cycle.
* **P6 crossings.** The clk side takes a write word (`w_hold`) at least
  2 clk after its last SCK rise and an address (`a_hold`) at least 2 clk
  after its last bit; the SCK side loads a read word into its output
  shifter at least 1 clk after the snapshot's data was captured in `buf0` or
  `buf1`. (The SDC bounds these cross-clock paths to one clk period.)

## Proof and its depth

`make -C formal hostport_ref` runs task `prove`: k-induction with
`smtbmc yices`, depth 20 steps (5 clk), base case from reset and inductive
step from any 20 consecutive steps that satisfy the contract and the
assertions. The design is the RTL above plus the reference; nothing is
bounded by the transaction length, so the result holds for transactions of
any length and any sequence of transactions and resets.

k-induction needs the properties strengthened with invariants that relate the
reference to the RTL's internal state (parser phase and counters against the
reference decoder, shift registers, the toggle synchronisers as a function
of clk rises since each event, the clk side's pending flags, addresses,
outstanding count and buffers, the output shifter against the snapshot
data). They are in the `LEMMAS` block, read only by the `prove` task, and
reach the RTL's internals through `connect -set` after `flatten`. They are
assertions like the properties, so they are proved too; they are not part of
the specification.

The RTL under proof is `src/pe_hostport.sv` as taped out (sha256
`05d2313596c411aecee3a733b63b0de02c9994a2aa947811edb1794868b28c38`),
unchanged by this work.

Runtime (single-threaded): `prove` 113 s and `cover` 187 s on runner1
(Core i5-13500); about 3 min and 515 s on a shared AMD EPYC 7282. The whole
`make -C formal`, which the CI job "Formal proofs and covers" runs, took
456 s on runner1 (9 of 9 tasks PASS; the job now expects 9).

## Covers (non-vacuity)

`make -C formal hostport_ref` also runs task `cover` (depth 230);
`make -C formal hostport_ref_cover1` covers the 1-bit transactions (depth
300, slow, not in `all`). Both use a guided subset of the contract (fixed
start phase, SCK edges only with a clk rise or half a clk from one), so every
trace they find is a legal one. Each property's trigger is reached:

| Cover | Step |
|---|---|
| quad WRITE: second word written, at addr + 1 | 146 |
| quad WRITE_FIXED: second word written, at addr | 146 |
| quad READ: second word committed | 178 |
| quad READ_FIXED: second word committed | 178 |
| CS_n rises mid-word after one complete word; the read is cancelled | 158 |
| CS_n rises mid-word in a write after one complete word | 110 |
| reset asserted with two read words snapped | 99 |
| reset asserted with a write word in flight | 101 |
| a write completes after a reset hit a counted transaction | 170 |
| a read word is committed after a reset hit a counted transaction | 202 |
| a write completes after SCK ran in a transaction held through reset | 122 |
| quad drivers on | 100 |
| a write after exactly LW (4 clk) | 118 |
| a commit after exactly LC | 150 |
| a cancel after exactly LX | 90 |
| 1-bit WRITE: a word written (`cover1`) | COVER1W |
| 1-bit READ: a word committed (`cover1`) | COVER1R |

The last three rows of the first group show the latency bounds are reached,
so 4 clk is exact for this contract.

## Mutation check

`make -C formal hostport_mutants` (`formal/hostport_mutants.py`, `JOBS=2` by
default) plants one bug at a time in a scratch copy of `pe_hostport.sv`
(outside `src/`), first checks that the unmutated RTL passes `prove`, then
runs task `mutant` for each: the guided subset of the contract, the
specification properties of one group only (no lemmas), up to 240 steps.
There, a violation sets a sticky flag that is asserted at the last step, so
the solver answers one satisfiable query instead of proving every
intermediate step; a mutant is killed only by a counterexample trace. The
groups are P1A/P2A (operations, addresses, order, latency), P1D/P2D (data),
P3, P5, P6.

| Mutant (one-line change) | Group | Violated at step |
|---|---|---|
| WRITE_FIXED increments the address | P1A | 174 |
| READ_FIXED increments the snapshot address | P2A | MUT_FRI |
| quad nibble bits reversed on input | P1A | MUT_QNR |
| quad write data rotated by a nibble | P1D | MUT_QWD |
| read word toggles its commit one SCK early | P2A | 142 |
| CS_n rising does not close the read (only the next address does) | P3 | MUT_CRN |
| a read's commit address not loaded from the new address | P2A | 150 |
| `host_ready` set right after reset, without seeing CS_n high | P1A | 126 |

Each trace was checked by hand for two of them: with the commit-early
mutant the commit comes while the host has clocked 7 of 8 nibbles; with
`host_ready` set early, a transaction selected across reset (CS_n fell
during reset) gets its write executed, which is review finding R3. All eight
are killed; the run took MUT_TIME on runner1 with 4 jobs.

## Not proved

* Behaviour outside the host contract (shorter CS_n gaps, SCK faster than
  clk, IO changing at a rise, SCK high while deselected, QUAD_EN changing
  mid-transaction): only `hostport.sby`'s count safety covers those.
* Power-up with CS_n low and an arbitrary SCK-side state (A1). Reset later
  reasserted with CS_n low is covered.
* Metastability and analog timing: the grid resolves coincident edges to the
  old value; P6 bounds the crossing margins in clk periods, and the SDC and
  post-route STA must hold them. Pad and board delays are outside the model.
* What the chip does with the bus operations: `bus_rdata` is free, so FIFO
  reservation and pop semantics of snapshot/commit/cancel in `pe_core` and
  `pe_chip` are not part of this proof, nor is the HOSTCFG write that sets
  QUAD_EN.
* The synthesized netlist: this is RTL only.
