from __future__ import annotations

import zlib


def compress_text(text: str | None) -> bytes | None:
    if not text:
        return None
    return zlib.compress(text.encode("utf-8"), level=6)


def decompress_text(blob: bytes | str | None) -> str | None:
    if not blob:
        return None
    if isinstance(blob, str):
        return blob
    return zlib.decompress(blob).decode("utf-8")
