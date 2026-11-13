"""Minimum signoff refuses malformed metrics and contradictory timing results.

These are fixture checks of the gate, not measurements of a hardened design.
The stricter release gate continues to require all-corner setup/electricals.
"""
import json

import pytest

from test_release_evidence import candidate, module


TIMING = [("hold", corner) for corner in module("signoff").CORNERS]
TIMING += [("setup", "nom_typ_1p20V_25C")]


@pytest.mark.parametrize("check,corner", TIMING)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, False, None, "0", -0.01])
def test_invalid_signoff_slack_is_refused(tmp_path, check, corner, value):
    run = candidate(tmp_path) / "runs/wokwi"
    path = run / "final/metrics.json"
    metrics = json.loads(path.read_text())
    metrics[f"timing__{check}__ws__corner:{corner}"] = value
    path.write_text(json.dumps(metrics))
    assert module("signoff").main(str(run)) == 1


@pytest.mark.parametrize("check,corner", TIMING)
@pytest.mark.parametrize("value", [1, -1, False, 0.0, float("nan"), None, "0"])
def test_invalid_signoff_violation_count_is_refused(tmp_path, check, corner, value):
    run = candidate(tmp_path) / "runs/wokwi"
    path = run / "final/metrics.json"
    metrics = json.loads(path.read_text())
    key = f"timing__{check}_vio__count__corner:{corner}"
    if value is None:
        del metrics[key]
    else:
        metrics[key] = value
    path.write_text(json.dumps(metrics))
    assert module("signoff").main(str(run)) == 1


@pytest.mark.parametrize("key", [key for _, key in module("signoff").MUST_BE_ZERO])
@pytest.mark.parametrize("value", [False, 0.0])
def test_physical_error_counts_require_integers(tmp_path, key, value):
    run = candidate(tmp_path) / "runs/wokwi"
    path = run / "final/metrics.json"
    metrics = json.loads(path.read_text())
    metrics[key] = value
    path.write_text(json.dumps(metrics))
    assert module("signoff").main(str(run)) == 1


def test_valid_minimum_signoff_still_allows_slow_setup_failure(tmp_path):
    run = candidate(tmp_path) / "runs/wokwi"
    path = run / "final/metrics.json"
    metrics = json.loads(path.read_text())
    slow = "nom_slow_1p08V_125C"
    metrics[f"timing__setup__ws__corner:{slow}"] = -0.5
    metrics[f"timing__setup_vio__count__corner:{slow}"] = 20
    path.write_text(json.dumps(metrics))
    assert module("signoff").main(str(run)) == 0
