"""Tiện ích dùng chung: kết nối kho, đọc bộ truy vấn OLAP."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "warehouse" / "olist_dw.duckdb"
OLAP_SQL = ROOT / "src" / "sql" / "olap_queries.sql"


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


@dataclass
class OlapQuery:
    qid: str
    operation: str
    question: str
    sql: str


def load_queries(path: Path = OLAP_SQL) -> list[OlapQuery]:
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^-- @query\s+(.+)$", text, flags=re.MULTILINE)
    out = []
    for i in range(1, len(parts), 2):
        qid, op, question = [s.strip() for s in parts[i].split("|", 2)]
        out.append(OlapQuery(qid, op, question, parts[i + 1].strip().rstrip(";")))
    return out
