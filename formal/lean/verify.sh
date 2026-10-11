#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
lake build
python3 audit_axioms.py
python3 check_model.py
python3 check_negative_controls.py
