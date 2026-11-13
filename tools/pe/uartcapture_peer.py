"""Fixed core1/F6 plans for optional UART capture beside autonomous usb_enum.

Load uart_rx_capture_peer.pasm while core1 is disabled. Before arm/rearm,
require core1 stopped/disabled and its RX/TX FIFOs empty. Preserve core0's
current enable bit; never clear its flags/FIFOs. F6 belongs to this workload.
The decoder is the unchanged declared-capture decoder; status/F6 are separate.
"""
from . import sim
from .profile import enable_mask
from .uartcapture import decode_capture


def capture_plan(length, *, core=1, enabled_before=0, profile=None, chip_id=None):
    """Validate all arguments, then return five ordinary core1/F6 writes.

    Caller first observes stopped/disabled target and empty target FIFOs.
    enabled_before must be the current CTRL enable mask, not a guessed mask,
    checked by profile.enable_mask (the chip ID's core count when chip_id
    is given, else the profile's); every other core's enable is preserved.
    No other-core register, configuration or FIFO writes are returned.
    """
    if type(length) is not int or not 1 <= length <= 32:
        raise ValueError("capture length must be an integer in 1..32")
    if type(core) is not int or core != 1:
        raise ValueError("peer UART capture supports only core1")
    peer = enable_mask(enabled_before, profile, chip_id) & ~2
    return [(sim.A_CTRL, peer),
            (sim.core_reg(1, sim.C_R0 + 1), length),
            (sim.core_reg(1, sim.C_R0 + 2), length % 4),
            (sim.A_FCLR, 64),
            (sim.A_CTRL, peer | 2 | (2 << 8))]
