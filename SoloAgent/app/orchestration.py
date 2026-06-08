# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Solo contributors
#
# Purpose:
# Compatibility surface for copied agent skills that expect orchestration primitives.

from __future__ import annotations

from typing import Any

from scratchpad import scratch_save


_SANDBOX_ENABLED = True


def get_sandbox_enabled() -> bool:
    return _SANDBOX_ENABLED


def set_sandbox_enabled(value: bool) -> bool:
    global _SANDBOX_ENABLED
    _SANDBOX_ENABLED = bool(value)
    return _SANDBOX_ENABLED


def delegate_subrun(
    *,
    prompt: str,
    instructions: str = "",
    max_iterations: int = 3,
    output_key: str = "",
    scratchpad_visible_keys: list[str] | None = None,
    tools_allowlist: list[str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic Solo delegate record.

    KoreAgent used a nested orchestration loop here. SoloAgent keeps the tool
    contract, but avoids spawning a hidden second LLM call until the SoloLLM
    orchestration layer grows explicit child-run controls.
    """
    answer = {
        "status": "deferred",
        "answer": "Delegate is available as a planning contract, but nested child runs are not enabled in SoloAgent yet.",
        "delegate_prompt": prompt,
        "instructions": instructions,
        "depth": 1,
        "max_iterations": max(1, int(max_iterations or 1)),
        "scratchpad_visible_keys": scratchpad_visible_keys or [],
        "tools_allowlist": tools_allowlist or [],
    }
    if output_key:
        scratch_save(output_key, answer["answer"])
    return answer
