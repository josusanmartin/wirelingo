"""
LibreLane plugin for protoemu's SRAM macros.

Adapted from kdp1965/ihp-um-janestreet-prism (Apache-2.0,
librelane_plugin_prism_pdn.py and odb_*.py), which integrates IHP SRAM macros
on the Tiny Tapeout SG13CMOS5L tile:

* Project.ExtendPowerStripes: the tile PDN is Metal4 stripes only, so the
  SRAMs' Metal4 power columns are fed by stripes drawn over them
  (flow/odb_stripes.py).
* Project.DiodesOnSramPins: an antenna diode on each SRAM signal input (the
  SRAM LEF has no antenna data) (flow/odb_sram_diodes.py).
* A fix for netgen's LVS JSON escaping of the SRAM power pin names (VDD! ...).
* Project.FixClockFanout: opt-in clock branch repair after CTS. It preserves
  each branch's loads and NDR while respecting the IHP fanout limit of eight.
* Project.SramClockAdvance: opt-in useful skew after CTS. Each SRAM's clock
  comes from a buffer on the clock tree's root net instead of CTS's balanced
  (delay-buffered) branch, so reads launch earlier (flow/sram_clock_advance.tcl);
  with PROTOEMU_CLOCK_FANOUT_FIX it also runs the clock branch repair.

LibreLane imports modules named librelane_plugin_* from the working directory
(the repo root, both locally and in the TT GDS action).
"""
import os
import sys

from librelane.steps import Step
from librelane.steps.odb import OdbpyStep
from librelane.config import Variable
from typing import List, Optional
from decimal import Decimal

from librelane.steps.step import CompositeStep
from librelane.steps.openroad import DetailedPlacement, OpenROADStep

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow")


@Step.factory.register()
class ExtendPowerStripes(OdbpyStep):
    id = "Project.ExtendPowerStripes"
    name = "Extend Power Stripes Over SRAMs"

    def get_script_path(self):
        return os.path.join(HERE, "odb_stripes.py")

    config_vars = [
        Variable("EXTEND_STRIPES_LAYER", str, "The tile's vertical single-layer PDN layer (Metal4 on the CMOS5L tile, TopMetal1 on the sg13g2 tile).", default="Metal4"),
        Variable("EXTEND_STRIPES_SRAM_LAYER", Optional[str], "Layer of the IHP SRAM power columns when it differs from the stripe layer; the column stripes then get via stacks down to it.", default=None),
        Variable("EXTEND_STRIPES_CLEARANCE", Decimal, "Spacing kept between a drawn stripe and the other net's macro rails or tile pins on the stripe layer.", units="µm", default=Decimal("0.24")),
        Variable("EXTEND_STRIPES_STACK_PITCH", Decimal, "Spacing of the via stacks along an SRAM power column.", units="µm", default=Decimal("10")),
        Variable("EXTEND_STRIPES_PIN_FACE_MARGIN", Decimal, "No rail via stack within this distance of a macro edge that carries pins (the stack would block the pins' escape); 0 leaves pdngen's stacks alone.", units="µm", default=Decimal("3")),
        Variable("EXTEND_STRIPES_SRAM_ALL_COLUMNS", bool, "Put a stripe on every legal supply column of an IHP SRAM rather than only the ones the tile grid and the per-region minimum need.", default=False),
        Variable("EXTEND_STRIPES_SRAM_ARRAY_EVERY_OTHER", bool, "In each bit-cell array of an IHP SRAM, add a VPWR/VGND stripe pair on every other available pair position (the grid's pairs stay; the macro's standard-cell band is left as the grid gives it).  Not with EXTEND_STRIPES_SRAM_ALL_COLUMNS.", default=False),
    ]

    def get_command(self):
        cmd = super().get_command() + [
            "--layer", self.config["EXTEND_STRIPES_LAYER"],
            "--clearance", str(self.config["EXTEND_STRIPES_CLEARANCE"]),
            "--stack-pitch", str(self.config["EXTEND_STRIPES_STACK_PITCH"]),
            "--pin-face-margin", str(self.config["EXTEND_STRIPES_PIN_FACE_MARGIN"]),
        ]
        if self.config.get("EXTEND_STRIPES_SRAM_LAYER"):
            cmd += ["--sram-layer", self.config["EXTEND_STRIPES_SRAM_LAYER"]]
        cmd += ["--sram-all-columns" if self.config["EXTEND_STRIPES_SRAM_ALL_COLUMNS"] else "--sram-grid-columns"]
        if self.config["EXTEND_STRIPES_SRAM_ARRAY_EVERY_OTHER"]:
            cmd += ["--sram-array-every-other"]
        return cmd


@Step.factory.register()
class SramPinDiodePlacement(OdbpyStep):
    id = "Project.SramPinDiodePlacement"
    name = "Diodes on SRAM Input Pins"

    def get_script_path(self):
        return os.path.join(HERE, "odb_sram_diodes.py")

    def get_command(self) -> List[str]:
        cell, pin = self.config["DIODE_CELL"].split("/")
        return super().get_command() + ["--diode-cell", cell, "--diode-pin", pin, "--macro-prefix", "RM_IHPSG13"]


@Step.factory.register()
class DiodesOnSramPins(CompositeStep):
    id = "Project.DiodesOnSramPins"
    name = "Diodes on SRAM Pins"
    Steps = [SramPinDiodePlacement, DetailedPlacement]


@Step.factory.register()
class ClockFanoutRepair(OpenROADStep):
    id = "Project.ClockFanoutRepair"
    name = "Split Overloaded IHP Clock Branches"

    def get_script_path(self):
        return os.path.join(HERE, "clock_fanout.tcl")


@Step.factory.register()
class FixClockFanout(CompositeStep):
    id = "Project.FixClockFanout"
    name = "Repair and Legalize Clock Branches"
    Steps = [ClockFanoutRepair, DetailedPlacement]


@Step.factory.register()
class SramClockAdvanceTcl(OpenROADStep):
    id = "Project.SramClockAdvanceTcl"
    name = "Advance the SRAM Clocks"

    config_vars = OpenROADStep.config_vars + [
        Variable("PROTOEMU_CLOCK_FANOUT_FIX", bool,
                 "Also split clock branches over the IHP fanout limit of eight (flow/clock_fanout_repair.tcl).",
                 default=False),
        Variable("PROTOEMU_LATE_CLOCK_REGS", Optional[List[str]],
                 "Glob patterns of Q net names whose registers get a delayed clock (flow/sram_clock_advance.tcl).",
                 default=None),
        Variable("PROTOEMU_LATE_CLOCK_STAGES", int,
                 "Clock buffers inserted in front of the PROTOEMU_LATE_CLOCK_REGS registers.",
                 default=2),
    ]

    def get_script_path(self):
        return os.path.join(HERE, "sram_clock_advance_step.tcl")


@Step.factory.register()
class SramClockAdvance(CompositeStep):
    id = "Project.SramClockAdvance"
    name = "Advance the SRAM Clocks and Legalize"
    Steps = [SramClockAdvanceTcl, DetailedPlacement]


# --- netgen writes the IHP SRAM's power pin names (VDD!, VSS!, VDDARRAY!) into
# its LVS JSON with a stray backslash ("\VDD!"), which is not a valid JSON
# escape, and librelane.steps.netgen.LVS then dies in json.loads before the
# LVS checker runs.  Give that module a json namespace whose loads() doubles
# such backslashes first (the metrics only count entries; names do not matter).
import json as _json
import re as _re
import types as _types

import librelane.steps.netgen as _netgen

_BAD_ESCAPE = _re.compile(r'\\(\\|[^"\\/bfnrtu])')


def _repair_escapes(s):
    return _BAD_ESCAPE.sub(lambda m: '\\\\' if m.group(1) == '\\' else '\\\\' + m.group(1), s)


def _loads_repairing_escapes(s, *args, **kwargs):
    try:
        return _json.loads(s, *args, **kwargs)
    except _json.JSONDecodeError:
        return _json.loads(_repair_escapes(s), *args, **kwargs)


_netgen.json = _types.SimpleNamespace(
    loads=_loads_repairing_escapes, load=_json.load, dumps=_json.dumps, dump=_json.dump,
    JSONDecodeError=_json.JSONDecodeError,
)
