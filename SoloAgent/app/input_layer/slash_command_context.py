# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Shared context passed to SoloAgent slash command handlers.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Callable


@dataclass
class SlashCommandContext:
    engine: Any
    request: dict[str, Any]
    output: Callable[[str, str], None]

