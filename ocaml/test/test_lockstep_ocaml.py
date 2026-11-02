"""test/test_lockstep.py with the OCaml model in the loop: three-way lockstep.

The tests are test_lockstep.py's, unedited, at its seeds. They compare the RTL
with tools/pe/sim.py every cycle; lock_chip.py compares sim.py with the OCaml
model on the same stimulus every cycle (WL_LOCK_EVERY=1, the default: the
complete state, the pins and the bus read). A state that differs between the
RTL and the OCaml model therefore fails one of the two comparisons.

ocaml/scripts/run_cocotb.sh lockstep3 runs it (WL_LOCK_OUT receives the totals).
SPDX-License-Identifier: Apache-2.0
"""
import os

import cocotb

import lock_chip

lock_chip.patch()

from test_lockstep import (test_call_ret, test_fifo_races, test_line_rx_jitter, test_random_lockstep,  # noqa: E402,F401
                           test_registers, test_wait_diff)


@cocotb.test()
async def test_zz_ocaml_model_agreed(dut):
    """Runs last: every chip of the tests above agreed with the OCaml model."""
    fails = lock_chip.finish_all()
    dut._log.info(f"OCaml model lockstep: {lock_chip.TOTALS}")
    assert not fails, fails[:2]
    floor = 2000 * int(os.environ.get("LOCKSTEP_SEEDS", "40"))
    assert lock_chip.TOTALS["cycles"] >= floor and not lock_chip.TOTALS["poked_chips"], lock_chip.TOTALS
