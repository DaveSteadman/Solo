# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Loads SoloReference host, port, data path, and logging defaults.

from __future__ import annotations

import sys
from pathlib import Path


SOLO_REFERENCE_ROOT = Path(__file__).resolve().parents[1]
SOLO_DATA_ROOT = SOLO_REFERENCE_ROOT.parent
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from common_utils.config import load_config, resolve_solo_path, service_host_port  # noqa: E402


_config = load_config()
_paths = _config.get("paths") if isinstance(_config.get("paths"), dict) else {}
_services = _config.get("services") if isinstance(_config.get("services"), dict) else {}
_service = _services.get("soloreference") if isinstance(_services.get("soloreference"), dict) else {}
_host, _port = service_host_port(_config, "soloreference", 9743)

cfg = {
    "host": _host,
    "port": _port,
    "data_dir": str(resolve_solo_path(_paths.get("soloDataReferenceRoot"), "./Data/SoloData/Reference")),
    "log_level": str(_service.get("log_level") or "info"),
}
