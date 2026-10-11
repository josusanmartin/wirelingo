"""Named firmware export exercised through the MicroPython model transport."""
import sys

import pytest

from pe import asm, export, host, sim
from pe.modelhost import ModelHost
from test_micropython_driver import FakeTT, protoemu
from test_spi_modes import FW, MOSI, SCK, MISO, TX, RESP, SpiPeer, step


@pytest.mark.parametrize("mode", range(4))
def test_named_spi_export_runs_on_relocated_core(monkeypatch, capsys, mode):
    monkeypatch.setattr(sys, "argv", [
        "pe.export", str(FW / "spi_master_modes.pasm"), "--core", "1",
        "--program", f"spi_master_cpha{mode & 1}", "--define", f"CPOL={mode >> 1}",
        "--out-pins", f"{MOSI},1", "--side-pins", str(SCK),
        "--in-pins", str(MISO), "--clkdiv", "4",
    ])
    export.main()
    snippet = capsys.readouterr().out
    mh = ModelHost()
    mh.write(sim.A_POUT, (mode >> 1) << SCK)
    mh.apply(host.pin_owner_writes({MOSI: 1, SCK: 1}))
    exec(snippet, {"pe": protoemu.ProtoEmu(FakeTT(mh))})
    peer = SpiPeer(mode, RESP)
    mh.ui = peer.miso
    for b in TX:
        mh.push(1, b << 24)
    mh.start(2)
    step(mh, peer, 1500)
    assert peer.rx == TX and mh.pop_all(1) == RESP
    assert len(peer.edges) == 16 * len(TX)
    assert not mh.chip.cores[1].fault


@pytest.mark.parametrize("source", [
    ".program other\nnop\n",
    ".program duplicate\nnop\n.program duplicate\nhalt\n",
])
def test_export_rejects_missing_or_ambiguous_program(monkeypatch, tmp_path, capsys, source):
    file = tmp_path / "programs.pasm"
    file.write_text(source)
    monkeypatch.setattr(sys, "argv", ["pe.export", str(file), "--program", "duplicate"])
    with pytest.raises(SystemExit) as error:
        export.main()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out and "must match exactly one .program" in output.err


def test_multi_program_export_requires_selection(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pe.export", str(FW / "spi_master_modes.pasm"),
                                      "--define", "CPOL=0"])
    with pytest.raises(asm.AsmError, match="expected one .program"):
        export.main()


def test_single_program_export_preserves_default(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pe.export", str(FW / "spi_master.pasm")])
    export.main()
    mh = ModelHost()
    exec(capsys.readouterr().out, {"pe": protoemu.ProtoEmu(FakeTT(mh))})
    assert mh.chip.fetch(0) == asm.assemble((FW / "spi_master.pasm").read_text()).words[0]
