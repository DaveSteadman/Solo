from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def sqlite_connection(path: Path, create_parent: bool = True) -> Iterator[sqlite3.Connection]:
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fts_build_query(query: str) -> str:
    token_re = re.compile(r'"([^"]+)"|(\()|(\))|(\|)|\b(AND|OR|NOT)\b|,|([^\s(),|]+)', re.IGNORECASE)

    def quote_term(value: str) -> str:
        clean = value.strip().replace('"', '""')
        return f'"{clean}"' if clean else ""

    out: list[str] = []
    open_parens = 0
    expect_operand = True
    for match in token_re.finditer((query or "").strip()):
        phrase, lparen, rparen, bar, keyword, word = match.groups()
        if phrase is not None:
            token = quote_term(phrase)
            if token:
                if not expect_operand:
                    out.append("AND")
                out.append(token)
                expect_operand = False
            continue
        if lparen:
            if not expect_operand:
                out.append("AND")
            out.append("(")
            open_parens += 1
            expect_operand = True
            continue
        if rparen:
            if open_parens > 0 and not expect_operand:
                out.append(")")
                open_parens -= 1
                expect_operand = False
            continue
        if bar or (keyword and keyword.upper() == "OR"):
            if not expect_operand:
                out.append("OR")
                expect_operand = True
            continue
        if keyword:
            op = keyword.upper()
            if op in {"AND", "NOT"} and not expect_operand:
                out.append(op)
                expect_operand = True
            continue
        if match.group(0) == ",":
            if not expect_operand:
                out.append("AND")
                expect_operand = True
            continue
        if word:
            token = quote_term(word)
            if token:
                if not expect_operand:
                    out.append("AND")
                out.append(token)
                expect_operand = False

    while out and out[-1] in {"AND", "OR", "NOT", "("}:
        if out.pop() == "(":
            open_parens = max(0, open_parens - 1)
    out.extend(")" for _ in range(open_parens) if out)
    return " ".join(out)


def compute_word_count(text: str | None) -> int | None:
    if not text:
        return None
    return len(text.split())
