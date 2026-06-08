# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Runs the SoloReference Wikipedia-snapshot article service.

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn


SOLO_REFERENCE_ROOT = Path(__file__).resolve().parent
SOLO_DATA_ROOT = SOLO_REFERENCE_ROOT.parent
if str(SOLO_REFERENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_REFERENCE_ROOT))
if str(SOLO_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLO_DATA_ROOT))

from app.config import cfg  # noqa: E402
from app.database import get_status, init_db  # noqa: E402
from common_utils.service import parse_service_args  # noqa: E402


def main() -> int:
    args = parse_service_args("Start the SoloReference service.")
    host = args.host or cfg["host"]
    port = int(args.port or cfg["port"])
    init_db()
    if args.command == "status" or args.dry_run:
        print({"service": "SoloReference", **get_status(), "dataRoot": cfg["data_dir"]})
        return 0
    print(f"SoloReference: http://{host}:{port}/ui", flush=True)
    uvicorn.run("app.server:app", host=host, port=port, log_level=cfg["log_level"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
