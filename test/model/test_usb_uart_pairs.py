"""The pins-only UART -> USB pair scenarios of test/test_usb_uart_rtl.py, on the model.

test_usb_uart_rtl.py runs each scenario (usb_uart_pairs.py) on the RTL top
level and on the cycle model and requires identical records; here the model
run alone is judged by the same independent expectations, so the scenario and
its oracle stay checked in the fast suite. With sigrok-cli present, sigrok's
uart and usb decoders also read the recorded pins: the UART bytes must be the
sender's, with a framing error on the bad-STOP frame only, and the USB packets
those the host model sent and decoded, in order, with no CRC or signalling error.
"""
import functools

import pytest

from pe import sigrok
import usb_uart_pairs as pairs

PERIOD_PS = 20834


@functools.lru_cache(maxsize=None)
def model_run(mode):
    rec, _ = pairs.run_model(mode)
    return rec


@pytest.mark.parametrize("mode", sorted(pairs.MODES))
def test_pair_scenario_on_model(mode):
    rec = model_run(mode)
    summary = pairs.check(mode, rec)
    assert summary["reports"] == [r.hex() for r in pairs.MODES[mode].expect(pairs.MODES[mode].frames())]


def _trace(rec):
    tr = sigrok.Trace()
    events = sorted((t, name, v) for name in ("rx", "dp", "dm") for t, v in rec["edges"][name])
    for t, name, v in events:
        tr.set(t * PERIOD_PS, name, v)
    tr.end(rec["t_end"] * PERIOD_PS)
    return tr


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
@pytest.mark.parametrize("mode", sorted(pairs.MODES))
def test_pair_scenario_sigrok(mode):
    rec = model_run(mode)
    tr = _trace(rec)
    data, warn = sigrok.uart(tr, "rx", 115200)
    assert [b for _, b in data] == [f["byte"] for f in rec["frames"]]
    bad = [f for f in rec["frames"] if not f["good"]]
    assert len(warn) == len(bad) == 1 and "rame" in warn[0][1], warn
    assert bad[0]["start"] * PERIOD_PS / 1000 <= warn[0][0] <= bad[0]["end"] * PERIOD_PS / 1000
    pk, err = sigrok.usb(tr, "dp", "dm", "low-speed")
    want = sigrok.usb_expect([bytes.fromhex(w[3]) for w in rec["wire"] if w[3]])
    assert sigrok.usb_summary(pk) == want
    assert not err and all(p.crc_ok for p in pk), err[:5]
