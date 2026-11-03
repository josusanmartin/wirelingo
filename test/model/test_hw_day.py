"""The hardware-day launcher, fpga/kv260/host/hw_day.py, against fakes and the cycle
model: nothing here touches a board. The rules it has to keep are in docs/hardware-day.md."""
import ast
import binascii
import json
import os
import pathlib
import re
import signal
import struct
import sys
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "fpga/kv260/host"))
import hw_day                                  # noqa: E402
import legacy_demo as ld                       # noqa: E402
import postfreeze_demo as pfd                  # noqa: E402
from pe import sim                             # noqa: E402

DOC = ROOT / "docs/hardware-day.md"
# xmutil's table in the format read on the board on 2026-11-03 (test_fabric_guard.py has the details)
from test_fabric_guard import table as listing     # noqa: E402
LISTAPPS = listing("protoemu40")


class FakeSpim:
    def __init__(self):
        self.calls, self.pad, self.quad, self.div = [], 0, False, 1
        self.mem = type("Mem", (), {"rd": lambda s, a: 0})()

    def pmod(self, mapping):
        self.calls.append(dict(mapping))

    def pmod_in(self):
        return self.pad() if callable(self.pad) else self.pad


class FakeRig:
    model, f_clk, f_meas = False, 40e6, 40e6
    ident = {"rtl_rev": "0" * 40, "clk_div": 30, "build_utc": "-"}

    def __init__(self):
        self.spim, self.closed, self.log, self.pout = FakeSpim(), 0, [], 0
        self.rx = {0: [], 1: []}

    def write(self, addr, words, fixed=False):
        self.log.append((addr, words))
        if addr == sim.A_POUT:
            self.pout = words

    def read(self, addr, n=1, fixed=False):
        for c in (0, 1):
            if addr == sim.core_reg(c, sim.C_STATUS):
                return min(len(self.rx[c]), 8) << 4
            if addr == sim.core_reg(c, sim.C_FIFO):
                out, self.rx[c] = self.rx[c][:n], self.rx[c][n:]
                return out[0] if n == 1 else out
        return 0

    def lab_mode(self, m):
        pass

    def pg_set(self, v):
        pass

    def gpio_uio(self, v):
        pass

    def wait(self, s):
        pass

    def burst(self, core, n, div, quad):
        return [0] * n

    def close(self):
        self.closed += 1


class Run:
    """run_step with scripted typing, a fake board and a temporary state file.
    `typed` answers every prompt in turn; EOF after that, as a closed stdin gives.
    `board(rig)` prepares each new fake."""

    def __init__(self, tmp):
        self.tmp, self.typed, self.rigs, self.out, self.board = tmp, [], [], [], lambda rig: None
        self.state = tmp / "state.json"
        (tmp / "proc").mkdir(exist_ok=True)

    def read(self, prompt):
        if not self.typed:
            raise EOFError
        answer = self.typed.pop(0)
        return answer(self.rigs[-1]) if callable(answer) else answer

    def make_rig(self):
        rig = FakeRig()
        self.board(rig)
        self.rigs.append(rig)
        return rig

    def __call__(self, name, apps=LISTAPPS):
        return hw_day.run_step(hw_day.BY_NAME[name], self.state, make_rig=self.make_rig, read=self.read,
                               out=self.out.append, listapps_fn=lambda: apps,
                               proc=str(self.tmp / "proc"), lock=str(self.tmp / "lock"))

    def confirm(self, *names):
        """These steps and all they depend on, just now."""
        s = json.loads(self.state.read_text()) if self.state.exists() else {}
        for n in names:
            s.update({x: time.time() for x in (n, *hw_day.closure(hw_day.BY_NAME[n]))})
        self.state.write_text(json.dumps(s))

    def confirmed(self):
        return set(json.loads(self.state.read_text()))


class Kernel:
    """The kernel's files the guard reads, kept agreeing with whatever listing a test gives:
    a good load of the active app, unless a test spoils them."""

    def __init__(self, tmp, monkeypatch):
        import fabric_guard
        self.fg, d = fabric_guard, tmp / "kernel"
        self.state, self.firmware, self.dir = d / "state", d / "firmware-name", d / "fw"
        for app in fabric_guard.KNOWN:
            (self.dir / app).mkdir(parents=True)
            (self.dir / app / f"{app}.bit.bin").write_bytes(b"x")
        self.state.write_text("operating\n")
        for name, value in (("FPGA_STATE", self.state), ("FIRMWARE_NAME", self.firmware), ("FIRMWARE_DIR", self.dir)):
            monkeypatch.setattr(fabric_guard, name, str(value))

    def loaded(self, text):
        try:
            app = self.fg.parse(text)
        except self.fg.NoActiveApp:
            return
        if app:
            self.firmware.write_bytes(f"{app}.bit.bin\0".encode())


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    k = Kernel(tmp_path, monkeypatch)
    k.loaded(LISTAPPS)
    return k


@pytest.fixture
def run(tmp_path, monkeypatch, kernel):
    monkeypatch.setattr(hw_day.time, "sleep", lambda s: None)
    monkeypatch.setattr(hw_day, "listapps", lambda: LISTAPPS)
    r = Run(tmp_path)

    class R(Run):
        def __call__(self, name, apps=LISTAPPS):
            kernel.loaded(apps)
            return Run.__call__(self, name, apps)
    r.__class__ = R
    return r


# ---------------------------------------------------------------- the order of the day

def closure(name):
    return hw_day.closure(hw_day.BY_NAME[name])


def test_every_prerequisite_comes_earlier():
    seen = set()
    for s in hw_day.STEPS:
        assert set(s.needs) <= seen, s.name
        seen.add(s.name)


def test_the_usb_wiring_comes_off_before_any_wire_goes_on_j2():
    for s in hw_day.STEPS:
        if s.pins or any("J2" in a for a, _ in s.wires):
            assert "usb-off" in closure(s.name), s.name
    removed = " ".join(hw_day.BY_NAME["usb-off"].remove)
    for needle in ("USB plug out first", "silkscreen 1 to a30", "silkscreen 3 to a20", "silkscreen 11 to i3",
                   "silkscreen 9 to b24", "black wire"):
        assert needle in removed


def test_step_16_takes_step_15s_wiring_off_plug_first():
    remove = hw_day.BY_NAME["rx"].remove
    assert remove[0].startswith("USB plug out")
    text = " ".join(remove)
    for needle in ("black wire", "silkscreen 1 to a30", "silkscreen 3 to a20", "silkscreen 11 to i3", "silkscreen 9 to b24"):
        assert needle in text
    assert any("USB plug" in x for x in hw_day.BY_NAME["rx"].off)


def test_the_isolated_cable_comes_before_the_switch():
    names = [s.name for s in hw_day.STEPS]
    assert names.index("tx-pi") < names.index("tx-switch") and "tx-pi" in closure("tx-switch")


@pytest.mark.parametrize("step", [s for s in hw_day.STEPS if s.pins], ids=lambda s: s.name)
def test_every_enabled_pin_is_a_signal_pin_in_the_wiring(step):
    text = " ".join(a for a, _ in step.wires)
    for p in step.pins:
        assert hw_day.SILK[p] <= 8                                   # 9-12 are rails: never mapped
        if not (step.name == "usb-pi" and p > 1):
            assert f"J2 silkscreen {hw_day.SILK[p]} " in text, (step.name, p)


def test_no_wire_joins_the_two_boards_rails():
    pi_wired = set()
    for s in hw_day.STEPS:
        for a, b in s.wires:
            assert not re.search(r"Pi pins? [24]\b", a + b), (s.name, a, b)     # 5 V
            if a.startswith("Pi pin") or "black" in a:
                pi_wired.add(s.name)
        if s.name in pi_wired:
            assert any("(GND)" in a for a, _ in s.wires), s.name
            if s.name != "usb-pi":
                assert not any(a.startswith("J2 silkscreen 11") for a, _ in s.wires), s.name
    assert pi_wired == {"uart", "spi", "i2c-capture", "i2c-clone", "i2c-mitm", "usb-pi"}


def test_no_ground_wire_on_the_ethernet_steps():
    for n in ("tx-pi", "tx-switch"):
        assert not any("GND" in a for a, _ in hw_day.BY_NAME[n].wires), n


def test_i2c_part_is_on_the_bench_when_the_chip_answers():
    for n in ("i2c-clone", "i2c-mitm"):
        s = hw_day.BY_NAME[n]
        assert not any(a.startswith("Part") for a, _ in s.wires)
        assert any("I2C part" in x for x in s.off)


def test_no_two_parts_lie_along_one_row():
    """Bare leads in a row can touch: a part may share a row only beside another."""
    extra = (("220 ohm", "d2 to d6"), ("220 ohm", "d12 to d16"))              # the second pair, on any transmit step
    for s in hw_day.STEPS:
        spans = {}
        wires = s.wires + (extra if s.wires == hw_day.RX_WIRES or s.wires[:6] == hw_day.TX_WIRES else ())
        for _, b in wires:
            m = re.fullmatch(r"([a-j])(\d+) to ([a-j])(\d+)", b)
            if m and m[1] == m[3]:
                spans.setdefault(m[1], []).append(sorted((int(m[2]), int(m[4]))))
        for row, parts in spans.items():
            parts.sort()
            for p, q in zip(parts, parts[1:]):
                assert p[1] <= q[0], (s.name, row, p, q)


def test_no_hole_is_used_twice_or_on_the_usb_columns():
    for s in hw_day.STEPS:
        if s.name == "usb-pi":
            continue
        holes = [h for a, b in s.wires for h in re.findall(r"\b([a-e]\d+)\b", b)]
        assert len(holes) == len(set(holes)), s.name
        assert not any(h[1:] in ("20", "30") for h in holes), s.name


def test_rx_divider_nets():
    """Columns: 3.3 V 17, pad 21, the pair's + 25, GND 29: the circuit the runbook draws."""
    wires = hw_day.RX_WIRES
    assert ("J2 silkscreen 11 (3.3 V)", "a17") in wires and ("J2 silkscreen 7 (RX in)", "a21") in wires
    assert ("RJ45 pin 1", "a25") in wires and ("RJ45 pin 2", "c29") in wires and ("J2 silkscreen 9 (GND)", "a29") in wires
    parts = [(a, tuple(sorted(int(x) for x in re.findall(r"\d+", b)))) for a, b in wires if a in ("220 ohm", "1 kohm")]
    assert parts.count(("1 kohm", (17, 21))) == parts.count(("1 kohm", (21, 25))) == parts.count(("1 kohm", (21, 29))) == 1
    assert parts.count(("220 ohm", (25, 29))) == 2


def test_nothing_moves_the_kria_cable():
    s = hw_day.BY_NAME["pi-net"]
    assert not any("Kria" in a + b for a, b in s.wires) and any("Kria's network cable" in x for x in s.off)
    assert "switch" not in hw_day.BY_NAME
    assert hw_day.BY_NAME["tx-switch"].needs == ("tx-pi",)


def test_rx_divider_estimate_stays_inside_the_rails():
    """The figures docs/hardware-day.md gives for the resistor-only receiver."""
    def par(a, b):
        return a * b / (a + b)
    gain = par(1000, 1000) / (1000 + par(1000, 1000))
    idle = 3.3 * par(1000, 1000) / (1000 + par(1000, 1000))
    assert gain == pytest.approx(1 / 3) and idle == pytest.approx(1.1)
    for vd, low, high in ((2.2, 0.37, 1.83), (2.5, 0.27, 1.93), (2.8, 0.17, 2.03)):
        assert idle - gain * vd == pytest.approx(low, abs=0.01) and idle + gain * vd == pytest.approx(high, abs=0.01)
    assert par(110, 1500) == pytest.approx(102.5, abs=0.1)


def test_tx_amplitude_estimates():
    """The drive-level figures docs/hardware-day.md labels as estimates."""
    def peak(rs, rd):
        return 3.3 * 100 / (100 + 2 * rs + 2 * rd)
    assert 0.50 < peak(220, 50) < 0.54 and 0.54 < peak(220, 30) < 0.56
    assert 0.76 < peak(110, 50) < 0.80 and 0.84 < peak(110, 30) < 0.87


# ---------------------------------------------------------------- refusing

def test_refuses_without_the_prerequisite(run):
    with pytest.raises(hw_day.Refused, match="usb-off"):
        run("rails")
    assert run.rigs == []


def test_a_step_confirmed_unlocks_the_next_and_only_then(run):
    run.typed = ["passed recover"]
    assert run("recover") == 0
    run.typed = ["passed preflight"]
    assert run("preflight") == 0
    run.typed = ["maybe"]
    assert run("usb-off") == hw_day.EXIT_FAILED
    assert "usb-off" not in run.confirmed()
    with pytest.raises(hw_day.Refused):
        run("rails")
    run.typed = ["passed usb-off"]
    assert run("usb-off") == 0
    assert run.confirmed() == {"recover", "preflight", "usb-off"}


def test_the_whole_chain_is_checked_not_just_the_direct_need(run):
    run.state.write_text(json.dumps({n: time.time() for n in ("tx-bench", "pi-prep")}))
    with pytest.raises(hw_day.Refused, match="recover"):
        run("tx-pi")
    run.state.write_text(json.dumps({"tx-pi": time.time()}))
    with pytest.raises(hw_day.Refused):
        run("rx")
    assert run.rigs == []


@pytest.mark.parametrize("step", [s for s in hw_day.STEPS if s.pins], ids=lambda s: s.name)
def test_forgetting_usb_off_stops_every_step_with_pins(run, step):
    run.confirm(*step.needs)
    assert hw_day.main(["--state", str(run.state), "forget", "usb-off"]) == 0
    with pytest.raises(hw_day.Refused, match="usb-off"):
        run(step.name)
    assert run.rigs == []


def test_forget_takes_back_only_that_step(run):
    run.confirm("usb-off")
    assert hw_day.main(["--state", str(run.state), "forget", "preflight"]) == 0
    assert run.confirmed() == {"recover", "usb-off"}


@pytest.mark.parametrize("age", [hw_day.MAX_AGE_S + 1, -3600])
def test_a_confirmation_expires(run, age):
    run.confirm("usb-off")
    s = json.loads(run.state.read_text())
    s["usb-off"] = time.time() - age
    run.state.write_text(json.dumps(s))
    with pytest.raises(hw_day.Refused, match="usb-off"):
        run("rails")


def test_an_old_state_format_confirms_nothing(run):
    run.state.write_text(json.dumps({"recover": "2026-11-03 10:00:00 UTC"}))
    with pytest.raises(hw_day.Refused, match="recover"):
        run("preflight")


@pytest.mark.parametrize("junk", ["{not json", "[1, 2]", '"recover"'])
def test_a_corrupt_state_file_refuses_in_words(run, junk):
    run.state.write_text(junk)
    with pytest.raises(hw_day.Refused, match="confirmations file"):
        run("preflight")


def test_list_shows_only_what_still_counts(run, capsys):
    run.confirm("recover")
    assert hw_day.main(["--state", str(run.state), "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "confirmed" in lines[0] and "confirmed" not in lines[1]


def usb_board(rig):
    class Pe:
        def write(self, *a, **k):
            pass

        def stop(self, *a):
            pass
    rig.ident = dict(FakeRig.ident, clk_div=25)
    rig.board = type("B", (), {"reset": lambda s: None, "pins": lambda s: (0, 0, 0, 0)})()
    rig.pe = Pe()
    rig.spim.pad = 1


USB_APP = listing("protoemu48")


def test_usb_pi_takes_back_usb_off_and_rx_then_refuses(run):
    run.confirm("usb-pi", "tx-pi")
    run.board = usb_board
    run.typed = ["vbus taped wired usb-pi", "", "passed usb-pi"]
    assert run("usb-pi", apps=USB_APP) == 0
    assert "usb-off" not in run.confirmed() and "usb-pi" in run.confirmed()
    with pytest.raises(hw_day.Refused, match="usb-off"):
        run("rx")
    run.typed = ["passed usb-off"]
    assert run("usb-off") == 0
    run.typed = []
    with pytest.raises(hw_day.Refused, match="nothing was enabled"):
        run("rx")                                                    # past the prerequisites, at the phrase


def test_usb_off_is_taken_back_even_if_usb_pi_fails(run, monkeypatch):
    run.confirm("usb-pi")
    monkeypatch.setattr(hw_day, "act_usb_pi", lambda ctx: hw_day.need(False, "no"))
    run.board = usb_board
    run.typed = ["vbus taped wired usb-pi"]
    assert run("usb-pi", apps=USB_APP) == hw_day.EXIT_FAILED
    assert "usb-off" not in run.confirmed()


def test_a_wrong_phrase_on_usb_pi_keeps_usb_off(run):
    run.confirm("usb-pi")
    run.typed = ["no"]
    with pytest.raises(hw_day.Refused):
        run("usb-pi", apps=USB_APP)
    assert "usb-off" in run.confirmed()


@pytest.mark.parametrize("name", ["cut", "ping", "rx"])
def test_every_pin_step_prints_its_wiring(name):
    lines = []
    hw_day.describe(hw_day.BY_NAME[name], lines.append)
    text = "\n".join(lines)
    assert "J2 silkscreen 7 (RX in)  ->  a21" in text and "Same as" not in text and "As step" not in text


@pytest.mark.parametrize("typed", [[], [""], ["wired"], ["WIRED TX-BENCH"], ["wired tx-pi"]])
def test_no_pin_without_the_exact_phrase(run, typed):
    run.confirm("preflight", "usb-off", "rails")
    run.typed = list(typed)
    with pytest.raises(hw_day.Refused, match="nothing was enabled"):
        run("tx-bench")
    assert run.rigs == []                                            # /dev/mem was never opened


def test_wiring_is_printed_before_the_phrase_is_asked(run):
    run.confirm("preflight", "usb-off", "rails")
    with pytest.raises(hw_day.Refused):
        run("tx-bench")
    text = "\n".join(run.out)
    assert "J2 silkscreen 1 (TD+)" in text and "MUST STAY DISCONNECTED" in text
    assert text.index("WIRING") < text.index("Type exactly `wired tx-bench`")


def test_tx_switch_needs_the_poe_phrase(run):
    run.confirm("preflight", "usb-off", "rails", "tx-bench", "pi-net", "pi-prep", "tx-pi")
    run.typed = ["wired tx-switch"]
    with pytest.raises(hw_day.Refused):
        run("tx-switch")
    assert run.rigs == []


@pytest.mark.parametrize("apps,why", [
    ("", "didn't print its table"),
    ("protoemu: Load Error: -1", "isn't a row"),
    (listing("k26-starter-kits"), "not one of ours"),
    (listing(None), "no active app"),
    (listing("protoemu48"), "needs protoemu40"),
])
def test_the_fabric_is_not_read_unless_the_right_app_is_active(run, apps, why):
    run.confirm("preflight", "usb-off", "rails")
    run.typed = ["wired tx-bench"]
    with pytest.raises(hw_day.Refused, match=why):
        run("tx-bench", apps=apps)
    assert run.rigs == []


def test_a_refusal_for_another_app_gives_the_load_one_step_at_a_time(run):
    run.confirm("rails")
    run.typed = ["wired tx-bench"]
    with pytest.raises(hw_day.Refused) as e:
        run("tx-bench", apps=listing("protoemu"))
    m = str(e.value)
    assert m.index("sudo xmutil unloadapp\n") < m.index("sudo xmutil loadapp protoemu40\n") < m.index("xmutil listapps")
    assert ";" not in m.split("one command at a time")[1].split("The table")[0] and "&&" not in m


def test_hw_day_knows_the_guards_apps():
    import fabric_guard
    assert tuple(hw_day.APPS) == fabric_guard.KNOWN


def test_the_launcher_has_no_override():
    assert "active-app" not in SOURCE and "PE_ACTIVE_APP" not in SOURCE and "told" not in SOURCE


def test_clock_divider_must_match_the_app(run, monkeypatch):
    run.confirm("preflight", "usb-off", "rails")
    run.typed = ["wired tx-bench"]
    monkeypatch.setattr(FakeRig, "ident", {"rtl_rev": "0" * 40, "clk_div": 24, "build_utc": "-"})
    with pytest.raises(hw_day.Refused, match="clk_div"):
        run("tx-bench")
    assert run.rigs[0].spim.calls == [] and run.rigs[0].closed == 1


def test_another_board_script_refuses(run):
    p = run.tmp / "proc" / "4242"
    p.mkdir()
    (p / "cmdline").write_bytes(b"python3\0fpga/kv260/host/pmod_test.py\0")
    run.confirm("preflight")
    with pytest.raises(hw_day.Refused, match="pmod_test.py"):
        run("usb-off")


def test_a_second_launcher_refuses(run):
    run.confirm("preflight")
    with hw_day.Lock(str(run.tmp / "lock")):
        with pytest.raises(hw_day.Refused, match="one process"):
            run("usb-off")


@pytest.mark.parametrize("name,needs", [("budget", ("preflight",)), ("ping", ("rx", "budget"))])
def test_a_step_on_eth_stack_refuses_in_a_tree_without_it(run, monkeypatch, name, needs):
    monkeypatch.setattr(hw_day, "root", lambda: run.tmp)
    run.confirm(*needs)
    with pytest.raises(hw_day.Refused, match="eth-stack"):
        run(name)


# ---------------------------------------------------------------- the pins

def bench(rig):
    """The right wiring: the read-back pad follows PIN_OUT bit rig.column, TD+ or TD-."""
    rig.column = 8
    rig.spim.pad = lambda: ((rig.pout >> rig.column) & 1) << hw_day.READ_PIN


def at(column):
    def answer(rig):
        rig.column = column
        return ""
    return answer


def logged_pmod(rig):
    real = rig.spim.pmod
    rig.spim.pmod = lambda m: (rig.log.append(("pmod", dict(m))), real(m))[1]


def test_tx_bench_sets_the_chip_up_before_enabling_pins(run):
    run.confirm("rails")

    def board(rig):
        bench(rig)
        logged_pmod(rig)
    run.board = board
    run.typed = ["wired tx-bench", at(8), at(9), "passed tx-bench"]
    assert run("tx-bench") == 0
    log = run.rigs[0].log
    first = next(i for i, e in enumerate(log) if e[0] == "pmod" and e[1])
    assert (sim.A_CTRL, 0b11 << 16) in log[:first] and (sim.A_POUT, 0) in log[:first]


def test_a_signal_during_the_turning_off_doesnt_interrupt_it(run):
    run.confirm("rails")

    def board(rig):
        bench(rig)
        real = rig.spim.pmod

        def pmod(m):
            if not m:
                os.kill(os.getpid(), signal.SIGTERM)
            real(m)
        rig.spim.pmod = pmod
    run.board = board
    run.typed = ["wired tx-bench", at(8), at(9), "passed tx-bench"]
    assert run("tx-bench") == 0
    rig = run.rigs[0]
    assert rig.spim.calls[-1] == {} and rig.closed == 1


class OffSpim:
    calls = []

    def pmod(self, m):
        self.calls.append(m)


def test_off_writes_the_pins_off(monkeypatch, capsys, kernel):
    monkeypatch.setattr(OffSpim, "calls", [])
    monkeypatch.setattr("pe_spim.Spim", OffSpim)
    hw_day.turn_off(lambda: LISTAPPS)
    assert OffSpim.calls == [{}] and "every PMOD pin is off" in capsys.readouterr().out


@pytest.mark.parametrize("table", [listing(None), "garbled", listing("k26-starter-kits")])
def test_off_never_claims_what_it_didnt_do(monkeypatch, kernel, table):
    monkeypatch.setattr(OffSpim, "calls", [])
    monkeypatch.setattr("pe_spim.Spim", OffSpim)
    with pytest.raises(hw_day.Refused, match="NOTHING WAS WRITTEN.*run nothing else"):
        hw_day.turn_off(lambda: table)
    assert OffSpim.calls == []


def test_off_refuses_when_the_device_tree_disagrees(monkeypatch, kernel):
    monkeypatch.setattr(OffSpim, "calls", [])
    monkeypatch.setattr("pe_spim.Spim", OffSpim)
    kernel.firmware.write_bytes(b"k26-starter-kits.bit.bin\0")
    with pytest.raises(hw_day.Refused, match="NOTHING WAS WRITTEN"):
        hw_day.turn_off(lambda: LISTAPPS)
    assert OffSpim.calls == []


def test_recover_never_expires_but_the_wiring_does(run):
    run.confirm("rails")
    s = json.loads(run.state.read_text())
    s["recover"] = time.time() - 30 * 86400                          # a month ago
    run.state.write_text(json.dumps(s))
    run.typed = []
    with pytest.raises(hw_day.Refused, match="nothing was enabled"):
        run("tx-bench")                                              # past the chain, at its own wiring prompt
    assert hw_day.fresh(s, "recover") and not hw_day.fresh({"usb-off": time.time() - 13 * 3600}, "usb-off")
    for bad in (True, float("nan"), "today", time.time() + 3600):
        assert not hw_day.fresh({"recover": bad}, "recover")


def test_step_0_says_dont_power_cycle_a_board_that_answers():
    doc = DOC.read_text()
    zero = doc[doc.index("(`recover`)"):doc.index("(`preflight`)")]
    assert "If it answers, do not power it off" in zero
    assert zero.index("If it answers, do not power it off") < zero.index("Unplug the Kria's power")
    assert "~/protoemu" in zero and "unguarded" in zero
    seq = zero[zero.index("sudo xmutil unloadapp"):]
    assert seq.index("sudo xmutil unloadapp") < seq.index("sudo xmutil loadapp protoemu") < seq.index("sudo xmutil listapps")


def test_nothing_tells_anyone_to_chain_the_load():
    # 2026-10-30: a chained load failed and the next command hung the board (fabric_guard.py)
    chained = re.compile(r"unloadapp\s*(;|&&)\s*(sudo\s+)?xmutil\s+loadapp|loadapp\s+[\w$]+\s*(;|&&)")
    files = [p for p in ROOT.rglob("*") if p.suffix in (".md", ".py", ".sh") and p.is_file()
             and not {".git", "ocaml", "sim_build"} & set(p.relative_to(ROOT).parts)
             and p.relative_to(ROOT).as_posix() != "test/mutants.py"]
    assert len(files) > 50
    found = [f"{p.relative_to(ROOT)}:{n}" for p in files
             for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1) if chained.search(line)]
    assert not found, found


def test_pins_on_after_the_phrase_and_off_at_the_end(run):
    run.confirm("preflight", "usb-off", "rails")
    run.board = bench
    run.typed = ["wired tx-bench", at(8), at(9), "passed tx-bench"]
    assert run("tx-bench") == 0
    rig = run.rigs[0]
    assert rig.spim.calls == [{0: hw_day.UO0, 1: hw_day.UO1}, {}] and rig.closed == 1
    assert "tx-bench" in run.confirmed()


def test_a_swapped_column_fails_and_turns_the_pins_off(run):
    run.confirm("preflight", "usb-off", "rails")
    run.board = bench
    run.typed = ["wired tx-bench", at(9), at(8), "passed tx-bench"]
    assert run("tx-bench") == hw_day.EXIT_FAILED
    assert run.rigs[0].spim.calls[-1] == {} and run.rigs[0].closed == 1
    assert "tx-bench" not in run.confirmed()


def test_guard_refuses_a_pin_the_step_did_not_name_and_turns_all_off():
    s = FakeSpim()
    g = hw_day.PinGuard(s, {0: {hw_day.UO0}})
    g.pmod({0: hw_day.UO0})
    with pytest.raises(hw_day.Refused):
        g.pmod({0: hw_day.UO0, 2: hw_day.UO0})
    assert s.calls == [{0: hw_day.UO0}, {}]
    with pytest.raises(hw_day.Refused):
        g.pmod({0: hw_day.UIO0})
    g.pmod({})


def test_refused_inside_a_step_turns_the_pins_off(run, monkeypatch):
    run.confirm("preflight", "usb-off", "rails", "tx-bench", "pi-prep", "pi-net")
    run.typed = ["wired tx-pi", "passed tx-pi"]
    monkeypatch.setattr(hw_day.BY_NAME["tx-pi"], "pins", {0: {hw_day.UO0}})         # one pin short
    assert run("tx-pi") == hw_day.EXIT_REFUSED
    assert run.rigs[0].spim.calls == [{}, {}] and run.rigs[0].closed == 1


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_a_signal_turns_the_pins_off(run, monkeypatch, sig):
    run.confirm("preflight", "usb-off", "rails", "tx-bench", "pi-prep", "pi-net")
    run.typed = ["wired tx-pi", "passed tx-pi"]
    before = signal.signal(sig, signal.SIG_IGN)     # without the launcher's handler, the test run survives to say so
    monkeypatch.setattr(hw_day, "tx_send", lambda *a: dropped_ssh())

    def dropped_ssh():
        os.kill(os.getpid(), sig)
        raise AssertionError("the signal should have raised first")
    try:
        assert run("tx-pi") == hw_day.EXIT_FAILED
        assert signal.getsignal(sig) == signal.SIG_IGN               # handlers put back
    finally:
        signal.signal(sig, before)
    rig = run.rigs[0]
    assert rig.spim.calls[-1] == {} and rig.closed == 1
    assert any("stopped by signal" in x for x in run.out)            # the handler, not the AssertionError


def test_an_unexpected_error_turns_the_pins_off(run, monkeypatch):
    run.confirm("preflight", "usb-off", "rails", "tx-bench", "pi-prep", "pi-net")
    run.typed = ["wired tx-pi", "passed tx-pi"]
    monkeypatch.setattr(hw_day, "tx_send", lambda *a: {}["no such thing"])
    assert run("tx-pi") == hw_day.EXIT_FAILED
    assert run.rigs[0].spim.calls[-1] == {} and run.rigs[0].closed == 1


def test_rails_reads_one_then_zero_with_no_pin_enabled(run):
    run.confirm("preflight", "usb-off")
    seq = iter([8] * 20 + [0] * 20 + [8] * 20)
    run.board = lambda rig: setattr(rig.spim, "pad", lambda: next(seq))
    run.typed = ["", "", "", "passed rails"]
    assert run("rails") == 0
    assert run.rigs[0].spim.calls == [{}]


def test_rails_stuck_high_stops_the_ground_wire(run):
    run.confirm("preflight", "usb-off")
    run.board = lambda rig: setattr(rig.spim, "pad", 8)            # also what an open pin 9 gives: the level held
    run.typed = ["", "", "", "passed rails"]
    assert run("rails") == hw_day.EXIT_FAILED
    assert "rails" not in run.confirmed()


def test_rails_pin_11_must_pull_the_pad_back_up(run):
    run.confirm("preflight", "usb-off")
    seq = iter([8] * 20 + [0] * 40)
    run.board = lambda rig: setattr(rig.spim, "pad", lambda: next(seq))
    run.typed = ["", "", "", "passed rails"]
    assert run("rails") == hw_day.EXIT_FAILED


def test_preflight_fails_if_a_pin_was_left_on(run):
    def on(rig):
        rig.spim.mem = type("Mem", (), {"rd": lambda s, a: 0x88})()
    run.board = on
    run.confirm("recover")
    run.typed = ["passed preflight"]
    assert run("preflight") == hw_day.EXIT_FAILED
    assert run.confirmed() == {"recover"}


def test_preflight_reports_a_missing_app(run, monkeypatch):
    monkeypatch.setattr(hw_day, "listapps", lambda: LISTAPPS.replace("protoemu48", "other"))
    run.confirm("recover")
    run.typed = ["passed preflight"]
    assert run("preflight") == hw_day.EXIT_FAILED
    assert any("MISSING protoemu48" in x for x in run.out)


def test_i2c_clone_releases_the_lines_before_mapping_them(run, monkeypatch):
    image = run.tmp / "image.json"
    image.write_text(json.dumps({"80": {"16": 0xF1, "17": 0x3F}}))
    monkeypatch.setattr(hw_day, "I2C_IMAGE", str(image))
    monkeypatch.setattr(hw_day, "act_i2c_clone", lambda ctx: hw_day.clone(ctx, 0.02))
    run.confirm("i2c-capture")

    def board(rig):
        rig.rx[1] = [0x100 | 0xA0, 0x200 | 0x10, 0x100 | 0xA1, 0x300, 0x500, 0x400]     # write pointer 0x10, read 1
        real = rig.spim.pmod
        rig.spim.pmod = lambda m: (rig.log.append(("pmod", dict(m))), real(m))
    run.board = board
    run.typed = ["wired i2c-clone", "passed i2c-clone"]
    assert run("i2c-clone") == 0
    log = run.rigs[0].log
    first_map = next(i for i, e in enumerate(log) if e[0] == "pmod" and e[1])
    before = log[:first_map]
    assert (sim.A_OD, 0b11) in before and (sim.A_POUT, 0b11) in before
    assert not any(a == sim.A_CTRL and w & 3 for a, w in before if a != "pmod")     # no core started yet
    assert [w for a, w in log if a == sim.core_reg(1, sim.C_FIFO)] == [0xF1]        # the captured byte, served


def test_man_in_the_middle_inverts_only_the_lowest_register():
    f = hw_day.invert_first({0x10: 0xF1, 0x11: 0x3F})
    assert f.reg == 0x10 and f(0x10, 0xF1) == 0x0E and f(0x11, 0x3F) == 0x3F


def test_the_whole_day_walks_through_in_order(run, monkeypatch):
    """Every step reachable, in the listed order, with its own phrases; the actions stubbed."""
    for s in hw_day.STEPS:
        assert not s.action or hasattr(hw_day, "act_" + s.action), s.name
        if s.action:
            monkeypatch.setattr(hw_day, "act_" + s.action, lambda ctx: None)
        monkeypatch.setattr(s, "files", ())
    for s in hw_day.STEPS:
        if s.name == "rx":                                           # usb-pi took it back
            run.typed = ["passed usb-off"]
            assert run("usb-off") == 0
        run.typed = ([s.typed()] if s.pins else []) + [f"passed {s.name}"]
        app = s.apps[0] if s.apps else "none"
        if s.apps:
            monkeypatch.setattr(FakeRig, "ident", dict(FakeRig.ident, clk_div=hw_day.APPS[app]))
        assert run(s.name, apps=listing(app if s.apps else "k26-starter-kits")) == 0, s.name
    assert run.confirmed() == set(hw_day.BY_NAME)


def la_words(states):
    """la_capture records for a list of channel states, 100 ticks apart."""
    return [0xF0 << 24 | states[0] << 16 | 0xFFFF] + [0xFF << 24 | v << 16 | (0xFFFF - 100) for v in states[1:]]


def spi_states(data):
    st, out = 0, [0b100, 0]
    for byte in data:
        for i in range(7, -1, -1):
            st = (st & ~0b10) | ((byte >> i & 1) << 1)
            out += [st, st | 1, st & ~1]
    return out + [st | 0b100]


def test_spi_step_decodes_a_capture_with_only_inputs_mapped(run, monkeypatch):
    run.confirm("uart")
    monkeypatch.setattr(hw_day, "act_spi", lambda ctx, f=hw_day.act_spi: f(ctx, seconds=0.05))
    run.board = lambda rig: rig.rx[0].extend(la_words(spi_states(b"WIRELINGO")))
    run.typed = ["wired spi", "passed spi"]
    assert run("spi") == 0
    assert any("WIRELINGO" in x and "0 gaps" in x for x in run.out)
    assert run.rigs[0].spim.calls == [{0: hw_day.UIO0, 1: hw_day.UIO1, 2: hw_day.UIO2}, {}]
    assert not any(a == sim.A_POE and w for a, w in run.rigs[0].log)   # the chip drives none of them


def i2c_states(seq):
    """SDA = bit 0, SCL = bit 1. seq: bytes clocked with an ACK, between START and STOP."""
    out = [0b11, 0b10, 0b00]
    for byte in seq:
        for bit in [(byte >> i) & 1 for i in range(7, -1, -1)] + [0]:
            out += [bit, bit | 2, bit]
    return out + [0, 2, 3]


def test_i2c_capture_saves_an_image_the_clone_can_load(run, monkeypatch):
    image = run.tmp / "image.json"
    monkeypatch.setattr(hw_day, "I2C_IMAGE", str(image))
    monkeypatch.setattr(hw_day, "act_i2c_capture", lambda ctx, f=hw_day.act_i2c_capture: f(ctx, seconds=0.05))
    run.confirm("rails", "pi-prep")
    run.board = lambda rig: rig.rx[0].extend(la_words(i2c_states([0xA0, 0x10, 0xCA])))
    run.typed = ["wired i2c-capture", "passed i2c-capture"]
    assert run("i2c-capture") == 0
    assert json.loads(image.read_text()) == {"80": {"16": 0xCA}}
    assert any("write to 0x50: 10 CA" in x for x in run.out)
    assert not any(e[0] in (sim.A_POE, sim.A_OD) and e[1] for e in run.rigs[0].log)   # it only listens


def test_usb_step_stays_inside_its_pins_through_the_real_usb_demo(run):
    """usb_demo.prepare and detach, unchanged, behind the guard: full-speed wiring, pull-up on silkscreen 1."""
    run.confirm("rails", "pi-prep")
    run.board = usb_board
    run.typed = ["vbus taped wired usb-pi", "", "passed usb-pi"]
    assert run("usb-pi", apps=USB_APP) == 0
    calls = run.rigs[0].spim.calls
    assert {1: hw_day.UIO0, 0: hw_day.UIO1} in calls and calls[-1] == {}


def test_rx_step_counts_frames_and_stops_on_a_pad_stuck_high(run):
    run.confirm("tx-pi")
    good = hw_day.pi_frame(hw_day.FIELD) + struct.pack("<I", binascii.crc32(hw_day.pi_frame(hw_day.FIELD)))
    run.board = lambda rig: setattr(rig, "burst", lambda *a: record_words(good))
    run.typed = ["pin risk accepted wired rx", "", "passed rx"]
    assert run("rx") == 0
    assert any("30 frames equal" in x for x in run.out)
    assert run.rigs[0].spim.calls == [{0: hw_day.UO0, 1: hw_day.UO1, 3: hw_day.UI0}, {}]

    def stuck(rig):                                                  # frames decode, and it still stops
        rig.burst = lambda *a: record_words(good)
        rig.spim.pad = 8
    run.board = stuck
    run.state.unlink()
    run.confirm("tx-pi")
    run.typed = ["pin risk accepted wired rx", "", "passed rx"]
    assert run("rx") == hw_day.EXIT_FAILED
    assert any("sits high" in x for x in run.out)


def test_ping_step_runs_the_station(run, monkeypatch):
    """eth-stack's classes are stand-ins here: this checks the launcher's side of them."""
    import types
    frames = [[b"arp"], [], []]

    class Host:
        def __init__(self, port, clock_hz):
            self.sent, self.stats = [], {}

        def configure(self, writes):
            pass

        def service(self):
            return frames.pop(0) if frames else []

        def send(self, raw):
            self.sent.append(raw)

    class Station:
        def __init__(self, mac, ip):
            self.stats = {"arp": 1}

        def handle(self, raw):
            return [b"reply"]
    eth = types.SimpleNamespace(mac=str, ip=str, Station=Station)
    ethhost = types.SimpleNamespace(Eth10Host=Host, load_writes=lambda f: [])
    monkeypatch.setitem(sys.modules, "pe.eth", eth)
    monkeypatch.setitem(sys.modules, "pe.ethhost", ethhost)
    # `from pe import eth` takes the package's attribute when an earlier test in this
    # process imported the real module: replace that too, or the test depends on order
    import pe
    monkeypatch.setattr(pe, "eth", eth, raising=False)
    monkeypatch.setattr(pe, "ethhost", ethhost, raising=False)
    monkeypatch.setattr(hw_day.BY_NAME["ping"], "files", ())
    monkeypatch.setattr(hw_day, "act_ping", lambda ctx, f=hw_day.act_ping: f(ctx, seconds=0.01))
    run.confirm("rx", "budget")
    run.typed = ["pin risk accepted wired ping", "passed ping"]
    assert run("ping") == 0
    rig = run.rigs[0]
    assert rig.spim.calls == [{0: hw_day.UO0, 1: hw_day.UO1, 3: hw_day.UI0}, {}] and rig.spim.quad is False


def test_uart_reports_what_the_pi_said(run, monkeypatch):
    run.confirm("rails", "pi-prep")
    monkeypatch.setattr(hw_day, "act_uart", lambda ctx, f=hw_day.act_uart: f(ctx, seconds=0.5))

    def board(rig):
        rig.rx[1] = list(b"HELLO FROM PI\n")
    run.board = board
    run.typed = ["wired uart", "passed uart"]
    assert run("uart") == 0
    assert run.rigs[0].spim.calls == [{0: hw_day.UO0, 3: hw_day.UI0}, {}]


# ---------------------------------------------------------------- pure helpers, and the cycle model

def wire_bits(fr):
    return [1, 0] * 31 + [1, 1] + [(b >> i) & 1 for b in fr for i in range(8)]


def test_tx_frame_is_a_legal_broadcast():
    fr = hw_day.tx_frame(7)
    assert len(fr) == 64 and fr[:6] == b"\xff" * 6 and fr[12:14] == b"\x88\xb5"
    assert struct.unpack("<I", fr[-4:])[0] == binascii.crc32(fr[:-4]) and b"WIRELINGO HW DAY 0007" in fr
    assert not fr[6] & 1 and fr[6] & 2                               # unicast source, locally administered


def test_tx_on_the_cycle_model_at_40_mhz():
    """eth10_tx as hw_day loads and feeds it, at the pads: exact half cells, the right bits."""
    rig = pfd.ModelRig(40e6)
    k = hw_day.t()
    pfd.setup(rig, owners={hw_day.UO0: 0, hw_day.UO1: 0})
    hw_day.tx_load(rig, k, 0)
    hw_day.ctrl(rig, k.sim, 0b01)
    rig.wait(2e-6)                                                   # past the first link pulse
    fr = hw_day.tx_frame(1)
    hw_day.tx_send(rig, k, 0, fr)
    want = wire_bits(fr)
    pads = []
    for _ in range(4 * len(want) + 400):
        rig.mh.step(1)
        pads.append(rig.mh.chip.outputs()[0] & 3)
    assert 3 not in pads
    s = next(i for i, v in enumerate(pads) if v)
    assert all(pads[s + 4 * i] == pads[s + 4 * i + 1] != pads[s + 4 * i + 2] == pads[s + 4 * i + 3]
               for i in range(len(want)))
    assert [1 if pads[s + 4 * i + 2] == 1 else 0 for i in range(len(want))] == want
    rig.wait(1e-5)
    hw_day.tx_wait(rig, k)                                           # F_GO cleared


def test_tx_wait_gives_up():
    class Stuck(FakeRig):
        def read(self, addr, n=1, fixed=False):
            return 1 << hw_day.F_GO
    with pytest.raises(RuntimeError, match="finish"):
        hw_day.tx_wait(Stuck(), hw_day.t(), polls=3)


def test_tx_send_refuses_a_frame_the_fifo_cant_hold():
    with pytest.raises(RuntimeError, match="streaming"):
        hw_day.tx_send(FakeRig(), hw_day.t(), 0, bytes(65))


def test_spi_decode_mode_0_msb_first():
    data = b"\xa5\x0f"
    st, t, edges = 0, 1, [(0, 0b100), (1, 0)]
    for byte in data:
        for i in range(7, -1, -1):
            st = (st & ~0b10) | ((byte >> i & 1) << 1)
            edges += [(t + 1, st), (t + 2, st | 1), (t + 3, st & ~1)]
            t += 3
    edges.append((t + 1, st | 0b100))
    assert hw_day.spi_decode(edges) == [data]


def test_pi_frames_suit_a_blind_burst_and_eth10_match():
    fr = hw_day.pi_frame(2000)
    assert len(fr) == 60 and fr[12:14] == b"\x88\xb5" and fr[14:16] == struct.pack("<H", 2000)
    wire = fr + struct.pack("<I", binascii.crc32(fr))
    assert "0" * 24 not in "".join(str((b >> i) & 1) for b in wire for i in range(8))


def record_words(fr):
    """eth10_rx's record for a frame: test_eth10_board.records."""
    wire = bytes([0x55] * 7 + [0xD5]) + fr
    bits = [(b >> k) & 1 for b in wire for k in range(8)]
    full, rem = divmod(len(bits), 24)
    words = [sum(b << (8 + k) for k, b in enumerate(bits[i * 24:i * 24 + 24])) for i in range(full)]
    if rem:
        words.append(sum(b << (32 - rem + k) for k, b in enumerate(bits[24 * full:])))
    return words + [(len(bits) << 16) | 0x80]


def test_rx_tally_counts_frames_noise_and_short_records():
    good = hw_day.pi_frame(2000) + struct.pack("<I", binascii.crc32(hw_day.pi_frame(2000)))
    bad = bytes([good[0] ^ 1]) + good[1:]
    pulse = [1 << 31, (2 << 16) | 0x80]
    assert hw_day.rx_tally(record_words(good) + [0, 0] + record_words(bad) + pulse + record_words(good), good) == (2, 1, 1)
    assert hw_day.rx_tally([0] * 100, good) == (0, 0, 0)


def cut_samples(period=5, rise_after=10, hit=True):
    """Idle, a frame's edges (half bits of 2 and 3 clocks), idle; bit 1 is HIT."""
    frame = []
    for k in range(2 * 576):
        frame += [k & 1] * (3 if k % 2 else 2)
    out = [0] * 200 + frame + [0] * 200
    if hit:
        for i in range(200 + 2 + period * hw_day.MATCH_LAST_BIT + rise_after, 200 + len(frame) - 1):
            out[i] |= 2
    return out


def test_analyse_cut_finds_the_rise_and_skips_cut_off_frames():
    sa = cut_samples()
    res = hw_day.analyse_cut(sa, 5)
    assert len(res) == 1 and res[0][2] and 7 <= res[0][0] <= 13 and res[0][1] > 1000
    assert hw_day.analyse_cut(sa[400:], 5) == []                     # no idle before it
    assert hw_day.analyse_cut(sa[:2000], 5) == []                    # no idle after it
    assert hw_day.analyse_cut(cut_samples(hit=False), 5) == [(None, None, True)]


class CutRig(pfd.ModelRig):
    """The cycle model with frames on uio0, as the pad behind the PMOD map would give
    them, and the sampler's trigger already high: the generator stands for the wire."""

    def __init__(self):
        super().__init__(50e6)
        self.spim = FakeSpim()

    def lab_mode(self, m):
        self.mh.mode = pfd.PG

    def pg_set(self, v):
        self.mh.pg_init, self.mh.pg = 4, None

    def gpio_uio(self, v):
        pass

    def burst(self, core, n, div, quad):
        fr = hw_day.pi_frame(hw_day.FIELD)
        fr += struct.pack("<I", binascii.crc32(fr))
        sched = []
        for i in range(3):
            sched += ld.manchester_sched(self, fr, t0_ns=20e3 + 200e3 * i)[0]
        self.pg_load([(t, v | 4) for t, v in sched], init=4)
        self.pg_start()
        return super().burst(core, n, div, quad)


def test_cut_sequence_on_the_cycle_model(monkeypatch):
    """Core 1 swapped from link pulses to the sampler; HIT at threshold 1000 on every
    whole frame, 10-11 clocks as docs/eth10-cut-through.md's model row; none at 3000."""
    monkeypatch.setattr(hw_day.time, "sleep", lambda s: None)
    rig = CutRig()
    lines = []
    ctx = hw_day.Ctx(hw_day.BY_NAME["cut"], rig, input, lines.append)
    hw_day.act_cut(ctx)
    first = ast.literal_eval(re.search(r"threshold 1000: (\[.*\]) \(clocks", lines[0]).group(1))
    assert first and all(10 <= r[0] <= 11 and r[2] for r in first)
    assert all(36e3 < r[1] * 20 < 39e3 for r in first)
    assert rig.spim.calls == [{0: hw_day.UO0, 1: hw_day.UO1, 3: hw_day.UIO0}] * 2


def test_cut_refuses_a_hit_below_the_threshold(monkeypatch):
    monkeypatch.setattr(hw_day.time, "sleep", lambda s: None)
    monkeypatch.setattr(hw_day, "analyse_cut", lambda *a: [(10, 1900, True)])
    ctx = hw_day.Ctx(hw_day.BY_NAME["cut"], CutRig(), input, lambda m: None)
    with pytest.raises(RuntimeError, match="below the threshold"):
        hw_day.act_cut(ctx)


def test_cut_refuses_no_hit_at_the_low_threshold(monkeypatch):
    monkeypatch.setattr(hw_day.time, "sleep", lambda s: None)
    monkeypatch.setattr(hw_day, "analyse_cut", lambda *a: [(None, None, True)])
    ctx = hw_day.Ctx(hw_day.BY_NAME["cut"], CutRig(), input, lambda m: None)
    with pytest.raises(RuntimeError, match="no HIT"):
        hw_day.act_cut(ctx)


# ---------------------------------------------------------------- on the Pi

def pi_files(tmp, model="Raspberry Pi 3 Model B Rev 1.2\0", route="Iface Destination\neth0 00000000 x\n", carrier="1"):
    (tmp / "model").write_text(model)
    (tmp / "route").write_text(route)
    d = tmp / "net/eth0"
    d.mkdir(parents=True, exist_ok=True)
    (d / "carrier").write_text(carrier)
    return dict(model=str(tmp / "model"), route=str(tmp / "route"), net=str(tmp / "net"))


def test_pi_send_refuses_off_a_pi(tmp_path):
    with pytest.raises(hw_day.Refused, match="Raspberry Pi only"):
        hw_day.pi_send("eth0", 1, 100, **pi_files(tmp_path, model="ZynqMP SMK-K26 Rev1/B/A\0"))


def test_pi_send_refuses_on_a_network(tmp_path):
    with pytest.raises(hw_day.Refused, match="default route"):
        hw_day.pi_send("eth0", 1, 100, **pi_files(tmp_path))


def test_pi_send_refuses_without_a_link(tmp_path):
    f = pi_files(tmp_path, route="Iface Destination\nwlan0 00000000 x\n", carrier="0")
    with pytest.raises(hw_day.Refused, match="no link"):
        hw_day.pi_send("eth0", 1, 100, **f)


def test_pi_watch_never_runs_off_a_pi(tmp_path, monkeypatch):
    monkeypatch.setattr(hw_day.subprocess, "Popen", lambda *a, **k: pytest.fail("tcpdump started"))
    f = pi_files(tmp_path, model="ZynqMP SMK-K26\0")
    with pytest.raises(hw_day.Refused, match="never tcpdump"):
        hw_day.pi_watch("eth0", 1, False, model=f["model"])


def test_a_missing_route_table_counts_as_on_a_network(tmp_path):
    assert hw_day.default_route_on("eth0", str(tmp_path / "none"))


# ---------------------------------------------------------------- what it must never do

SOURCE = (ROOT / "fpga/kv260/host/hw_day.py").read_text()


def test_recover_opens_nothing_and_comes_first(run):
    assert hw_day.STEPS[0].name == "recover" and "recover" in closure("preflight")
    s = hw_day.STEPS[0]
    assert not s.action and not s.pins and not s.apps
    run.typed = ["passed recover"]
    assert run("recover", apps="") == 0                              # works with no table at all
    assert run.rigs == []
    for needle in ("xmutil listapps", "dmesg", "journalctl -u dfx-mgr"):
        assert any(needle in c for c in s.kria)
    for c in s.kria:
        assert "loadapp" not in c and "/dev/mem" not in c and "bringup" not in c


def test_a_refused_load_says_stop(kernel):
    with pytest.raises(hw_day.Refused, match="stop, run nothing"):
        hw_day.check_app(hw_day.BY_NAME["usb-pi"], lambda: LISTAPPS)


def test_the_incident_stops_every_board_step_before_the_rig(run, monkeypatch):
    """2026-11-03: k26-starter-kits held the slot after a failed load. No step opens anything."""
    from test_fabric_guard import BOARD_2026_11_03
    for s in hw_day.STEPS:
        monkeypatch.setattr(s, "files", ())
    for s in hw_day.STEPS:
        if s.action:
            run.state.unlink(missing_ok=True)
            run.confirm(s.name)
            run.typed = [s.typed(), f"passed {s.name}"] if s.pins else [f"passed {s.name}"]
            with pytest.raises(hw_day.Refused, match="k26-starter-kits"):
                run(s.name, apps=BOARD_2026_11_03)
    assert run.rigs == []


def test_turn_off_opens_nothing_without_an_app(monkeypatch, kernel):
    monkeypatch.setattr("os.open", lambda *a, **k: pytest.fail("opened"))
    with pytest.raises(hw_day.Refused, match="NOTHING WAS WRITTEN"):
        hw_day.turn_off(lambda: listing(None))


def test_source_never_reboots_or_touches_the_network_or_runs_pmod_test():
    tree = ast.parse(SOURCE)
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "pmod_test" not in imports
    runs = [ast.get_source_segment(SOURCE, n) for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr in ("run", "Popen", "system", "call", "check_call")]
    assert len(runs) == 1 and "tcpdump" in runs[0]                   # xmutil only through fabric_guard
    for word in ("shutdown", "poweroff", "swd", "SWD", "systemctl", "ethtool", "ifconfig", "os.system"):
        assert word not in SOURCE, word


def load_at(path):
    """hw_day's source as if installed at `path`: the Pi's copy has no tree around it."""
    mod = types.ModuleType("hw_day_on_the_pi")
    mod.__file__ = path
    sys.modules[mod.__name__] = mod
    try:
        exec(compile(SOURCE, path, "exec"), mod.__dict__)
    except BaseException:
        del sys.modules[mod.__name__]
        raise
    return mod


@pytest.mark.parametrize("path", ["/home/pi/hw_day.py", "/root/hw_day.py", "/hw_day.py"])
def test_it_loads_and_runs_from_a_short_path(tmp_path, capsys, path):
    mod = load_at(path)
    try:
        assert mod.main(["--state", str(tmp_path / "s.json"), "list"]) == 0
        assert mod.main(["--state", str(tmp_path / "s.json"), "show", "rx"]) == 0
        assert mod.main(["pi-send", "--iface", "nothing"]) == mod.EXIT_REFUSED      # off a Pi: refused, not a crash
        assert mod.main(["pi-watch", "--iface", "nothing"]) == mod.EXIT_REFUSED
    finally:
        del sys.modules[mod.__name__]
    assert "IndexError" not in capsys.readouterr().err


def test_the_eth_stack_list_has_all_eth_tap_needs():
    assert {"tools/pe/eth.py", "tools/pe/ethhost.py", "tools/pe/ethsim.py", "firmware/eth10_tx_cs.pasm",
            "fpga/kv260/host/eth_tap.py"} == set(hw_day.ETH_STACK)
    doc = DOC.read_text()
    assert all(f in doc for f in hw_day.ETH_STACK)                   # the setup's copy line


def test_a_pi_copy_imports_nothing_from_the_board():
    tree = ast.parse(SOURCE)
    top = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    top |= {n.module for n in tree.body if isinstance(n, ast.ImportFrom)}
    assert top <= set(sys.stdlib_module_names)


def test_list_and_show_touch_nothing(tmp_path, capsys):
    state = str(tmp_path / "s.json")
    assert hw_day.main(["--state", state, "list"]) == 0
    assert hw_day.main(["--state", state, "show", "rx"]) == 0
    out = capsys.readouterr().out
    assert "preflight" in out and "RJ45 pin 1" in out


def test_refusal_is_exit_2_with_a_reason(tmp_path, capsys):
    assert hw_day.main(["--state", str(tmp_path / "s.json"), "run", "rails"]) == hw_day.EXIT_REFUSED
    assert "usb-off" in capsys.readouterr().err


# ---------------------------------------------------------------- the runbook matches the launcher

def test_runbook_has_every_step_in_order_with_this_wiring():
    doc = DOC.read_text()
    assert re.findall(r"^\s*hd run ([\w-]+)", doc, re.M) == [s.name for s in hw_day.STEPS]
    parts = re.split(r"^### Step \d+: ", doc, flags=re.M)[1:]
    assert len(parts) == len(hw_day.STEPS)
    for s, text in zip(hw_day.STEPS, parts):
        assert f"`{s.name}`" in text.splitlines()[0], s.name
        for a, b in s.wires:
            assert f"| {a} | {b} |" in text, (s.name, a, b)
        for c in s.pi:
            if not c.startswith("restart"):
                assert c in text, (s.name, c)
        for c in s.kria:
            assert c in text, (s.name, c)
        for word in ("Must stay disconnected", "Success", "Failure", "Risk"):
            assert word in text, (s.name, word)


def test_the_hard_rules_have_no_exceptions():
    doc = DOC.read_text()
    five = re.search(r"^5\. (.*?)^6\. ", doc, re.M | re.S)[1]
    six = re.search(r"^6\. (.*?)^7\. ", doc, re.M | re.S)[1]
    assert five.startswith("**Never run `pmod_test.py`.**") and six.startswith("**Never touch eth0 on the Kria.**")
    for rule in (five, six):
        assert "exception" not in rule.replace("No exceptions", "") and " unless " not in rule and " but " not in rule
    assert "far end" not in doc and "Move the Kria" not in doc


def test_setup_makes_the_directory_before_rsync_needs_it():
    doc = DOC.read_text()
    setup = doc[doc.index("## Setting up"):doc.index("### The fabric guard")]
    assert setup.index("mkdir -p hwday/fpga/kv260") < setup.index("rsync")


def test_step_15_sends_you_back_to_usb_off():
    doc = DOC.read_text()
    assert "`hd run usb-off`" in doc[doc.index("(`usb-pi`)"):doc.index("(`rx`)")]


def test_runbook_states_the_rules():
    doc = DOC.read_text().lower()
    for needle in ("48 v", "pmod_test.py", "check before wiring", "eth0", "one process", "reboot", "shared ground",
                   "estimate", "squelch", "no swd", "vbus", "load error", "no tool"):
        assert needle in doc, needle
