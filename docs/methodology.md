# Building an ASIC with AI agents, without chip-design experience

This chip was designed by one person with no prior ASIC or RTL experience,
working with AI agents that wrote nearly all of the specification, RTL,
models, firmware, tests and flow scripts. <!-- TODO(author): one or two
sentences on your background and when you started; the git history begins on
2026-09-30 with "v0.2", so say what came before. -->

The interesting question isn't whether an AI can write Verilog. It is how you
can trust a chip you couldn't have written yourself. This page describes what
the agents did, what the human did, and the evidence that stands between an
agent's claim and the GDS.

## Who did what

| Role | Who | What it did here |
|---|---|---|
| Direction and judgment | the author | goals and scope (protocols, tile size, two cores), what to cut, when to stop exploring, every spec change, hardware and accounts (FPGA board, licenses, build machines) |
| Engineering | Claude (Claude Code) | ISA spec, golden model, assembler, RTL, firmware, cocotb/formal/mutation tests, LibreLane flow, physical experiments, FPGA bring-up |
| Independent review | Codex | read the repo cold, reproduced bugs with tests and proposed fixes ([review.md](review.md)) |
| Parallel work | Claude sub-agents | self-contained tasks in their own git worktrees (FPGA bring-up, the reverse-engineering demo), reporting measured results back |

129 of the repository's 149 commits are co-authored by Claude.

## The rule: no claim without independent evidence

Agents write fluent, confident text, and they make mistakes. The ones that
matter here are confident: "the fix is done", "this meets timing", "exactly
equivalent". So nothing in this repository rests on an agent's word. Each
claim has to be checked by something that doesn't share the agent's
assumptions:

* **Two implementations of one spec.** [isa.md](isa.md) is normative. A
  cycle-accurate Python model and the RTL implement it separately, and
  constrained-random programs, pins and host traffic run on both, compared
  every cycle ([verification.md](verification.md) §2). An agent that
  misreads the spec has to misread it the same way twice, in two languages.
* **Formal proofs** for the parts where a random test can miss a corner: the
  FIFOs against reference queues, host-port bus safety, core invariants, the
  line unit's phase tracking.
* **Mutation testing** checks the checks. Injected bugs in the model, RTL,
  firmware and assembler must each make a named test fail
  (`test/mutants.py`, 17 of 17 killed). Tests that pass whatever the code
  does are worthless, and agents write those too.
* **Device models that see only pins.** Firmware is tested against
  independent models of a UART, an I2C EEPROM, a USB host and so on, not
  against the code that drives them.
* **Physical signoff by script.** `flow/signoff.py` applies Tiny Tapeout's
  criteria to the run's own reports, and Tiny Tapeout's precheck runs on the
  GDS. The gate-level netlist reruns the top-level tests.
* **Hardware.** The RTL runs on a Kria KV260 FPGA at the chip's 50 MHz:
  firmware loads, UART crosses real pins, and the quad host port moves
  193.6 Mbit/s, the figure simulation predicted ([fpga/kv260](../fpga/kv260/README.md)).
* **A second agent as adversary.** Codex reviewed the work cold, and Claude
  reviewed Codex's fixes. Neither was trusted to grade its own work.

## What this caught

Each of these was written or approved by an agent, and caught by one of the
mechanisms above rather than by the agent that made it:

| What went wrong | Caught by |
|---|---|
| The assembler encoded `nop` with an old field layout | a firmware test on the model (USB bit count off) |
| A global reset during an SPI transaction left the host port mid-command (R3) | Codex's review, reproduced as a test |
| The timing constraints cut every SCK/clk path, so the crossings were never timed (R2) | Codex's review |
| A USB device accepted a DATA packet with a corrupt PID (R4) | Codex's review |
| Codex's own signoff gate demanded slow-corner timing Tiny Tapeout doesn't require, so no run could ever pass | Claude reviewing the review |
| Codex's reset fix was correct, but its netlists congested routing | physical experiments: a dozen place-and-route runs, then a different global-routing setting |
| The two-core USB firmware took its own ACK for the host's | the concurrent RTL service test, once an unrelated cleanup made the host software faster |
| Claude described the FPGA as running "exactly" and "at full speed" before either was measured | a second review of that explanation; the claims were then measured on the board |

The full lists are in [verification.md](verification.md) (bugs found by the
flow) and [review.md](review.md) (the independent review).

## What the human had to do

* **Set direction and cut scope.** Agents will happily keep optimizing.
  Deciding that two cores, not three, is the tapeout configuration, that a
  clean repository and signoff come before new features, and when an
  exploration had run long enough were the author's calls.
* **Own the spec changes.** Dropping cross-bank instruction fetch made the
  chip routable but changed what programs can do; that was the author's
  decision, not the agent's.
* **Provide the real world.** Accounts, licenses, build machines and the FPGA
  board, and the decision of what to publish and when.
* **Ask "how do you know?"** Most of the value came from refusing a
  summary until it pointed at a test, a report or a measurement.
<!-- TODO(author): anything you'd add in your own words: what surprised you,
what you'd do differently, how much time it took. -->

## Limits

* The agents made every engineering decision below the level of goals and
  scope. The verification is broad, but it checks the spec as the agents
  wrote it; it can't catch a spec that asks for the wrong thing.
* Nothing formally proves that synthesis preserved the RTL's behaviour;
  the gate-level tests cover the exercised cases.
* The FPGA validates the logic at speed, not the IHP silicon: pads, real
  timing and the latch FIFOs are only covered by static timing analysis and,
  eventually, the chip itself.
