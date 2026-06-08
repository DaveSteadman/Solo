# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Backward-compatible launcher for SoloDataGateway.

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path


GATEWAY_ROOT = Path(__file__).resolve().parent / "solo_data_gateway"
GATEWAY_MAIN = GATEWAY_ROOT / "main.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("solo_data_gateway_main", GATEWAY_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {GATEWAY_MAIN}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
