"""ETL orchestrator cho kho dữ liệu Olist.

Chạy:
    python src/etl/pipeline.py                      # reset + initial load (đến 2018-01-01) + 10 batch incremental theo tháng
    python src/etl/pipeline.py --initial-only       # chỉ initial load
    python src/etl/pipeline.py --cutoff 2018-11-01  # chạy thêm 1 batch incremental tới mốc chỉ định
    OLIST_RAW_DIR=data/sample OLIST_DB=warehouse/sample.duckdb python src/etl/pipeline.py   # chạy trên dữ liệu mẫu

Luồng mỗi lần chạy:
    01_staging.sql   Extract CSV -> stg.raw_* -> làm sạch stg.*
    02/03            (chỉ khi --reset) tạo schema & dimension tĩnh
    10_batch_load    Transform + Load cho từng batch [watermark, cutoff)
    04_mart.sql      làm mới view / aggregate
    05_dq_checks     kiểm tra DQ; lỗi RI/đối soát -> dừng (fail-fast)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "src" / "sql"
RAW_DIR = Path(os.environ.get("OLIST_RAW_DIR", ROOT / "data" / "raw"))
REF_DIR = ROOT / "src" / "etl" / "reference"
DB_PATH = Path(os.environ.get("OLIST_DB", ROOT / "warehouse" / "olist_dw.duckdb"))

EPOCH = datetime(1900, 1, 1)
INITIAL_CUTOFF = datetime(2018, 1, 1)
FINAL_CUTOFF = datetime(2018, 11, 1)

TRACKED_TABLES = [
    "dw.dim_geography", "dw.dim_product", "dw.dim_seller", "dw.dim_customer",
    "dw.fact_sales", "dw.fact_order_fulfillment", "dw.fact_payment", "dw.fact_review",
    "dw.bridge_order_seller", "dw.fact_seller_monthly_snapshot",
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def read_sql(name: str) -> str:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    return sql.replace("${RAW_DIR}", RAW_DIR.as_posix()).replace("${REF_DIR}", REF_DIR.as_posix())


def split_steps(sql: str) -> list[tuple[str, str]]:
    """Tách file SQL theo marker '-- @step <tên>'."""
    parts = re.split(r"^-- @step\s+(\w+)\s*$", sql, flags=re.MULTILINE)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]


def month_starts(start: datetime, end: datetime) -> list[datetime]:
    out, y, m = [], start.year, start.month
    while True:
        m += 1
        if m == 13:
            y, m = y + 1, 1
        d = datetime(y, m, 1)
        if d > end:
            return out
        out.append(d)


class Pipeline:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(db_path))

    # ------------------------------------------------------------------
    def reset(self) -> None:
        log("RESET: xóa schema dw/etl/mart")
        for s in ("mart", "dw", "etl"):
            self.con.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")

    def extract_and_clean(self) -> None:
        t = time.perf_counter()
        self.con.execute(read_sql("01_staging.sql"))
        n = self.con.execute("SELECT count(*) FROM stg.raw_orders").fetchone()[0]
        log(f"EXTRACT+CLEAN: staging xong ({n:,} đơn hàng nguồn) trong {time.perf_counter() - t:.1f}s")

    def ensure_schema(self) -> None:
        self.con.execute(read_sql("02_dw_schema.sql"))
        self.con.execute(read_sql("03_static_dims.sql"))

    def watermark(self) -> datetime:
        row = self.con.execute("SELECT value FROM etl.watermark WHERE name = 'orders.purchase_ts'").fetchone()
        return row[0] if row else EPOCH

    def counts(self) -> dict[str, int]:
        return {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TRACKED_TABLES}

    # ------------------------------------------------------------------
    def run_batch(self, cutoff: datetime, batch_type: str) -> None:
        prev = self.watermark()
        if cutoff <= prev:
            log(f"Bỏ qua: cutoff {cutoff:%Y-%m-%d} <= watermark {prev:%Y-%m-%d}")
            return
        batch_id = (self.con.execute("SELECT coalesce(max(batch_id), 0) + 1 FROM etl.batch_log").fetchone()[0])
        started = datetime.now()
        t0 = time.perf_counter()
        self.con.execute(f"SET VARIABLE batch_id = {batch_id}")
        self.con.execute(f"SET VARIABLE prev_cutoff = TIMESTAMP '{prev:%Y-%m-%d %H:%M:%S}'")
        self.con.execute(f"SET VARIABLE cutoff = TIMESTAMP '{cutoff:%Y-%m-%d %H:%M:%S}'")

        before = self.counts()
        updated: dict[str, int] = {}
        self.con.execute("BEGIN TRANSACTION")
        try:
            for step, sql in split_steps(read_sql("10_batch_load.sql")):
                ts = time.perf_counter()
                self.con.execute(sql)
                if step == "dim_customer_scd2":
                    updated["dw.dim_customer"] = self.con.execute(
                        "SELECT count(DISTINCT uid) FROM cust_changes c "
                        "WHERE EXISTS (SELECT 1 FROM cust_prev p WHERE p.uid = c.uid)").fetchone()[0]
                elif step == "fact_order_fulfillment":
                    updated["dw.fact_order_fulfillment"] = self.con.execute(
                        "SELECT count(*) FROM b_fulfill_changed").fetchone()[0]
                log(f"  batch {batch_id} | {step:<30s} {time.perf_counter() - ts:6.2f}s")
            self.con.execute(read_sql("04_mart.sql"))
            self.con.execute(read_sql("05_dq_checks.sql"))
            fails = self.con.execute(
                "SELECT check_id, description, affected_rows FROM etl.dq_result "
                "WHERE batch_id = ? AND status = 'FAIL'", [batch_id]).fetchall()
            if fails:
                raise RuntimeError(f"DQ FAIL: {fails}")

            after = self.counts()
            for t in TRACKED_TABLES:
                self.con.execute("INSERT INTO etl.load_stats VALUES (?, ?, ?, ?)",
                                 [batch_id, t, after[t] - before[t], updated.get(t, 0)])
            self.con.execute("INSERT OR REPLACE INTO etl.watermark VALUES ('orders.purchase_ts', ?)", [cutoff])
            dur = time.perf_counter() - t0
            self.con.execute("INSERT INTO etl.batch_log VALUES (?, ?, ?, ?, ?, ?, ?, 'SUCCESS', ?)",
                             [batch_id, batch_type, prev, cutoff, started, datetime.now(), dur,
                              f"+{after['dw.fact_sales'] - before['dw.fact_sales']} sales rows, "
                              f"{updated.get('dw.fact_order_fulfillment', 0)} fulfillment rows updated"])
            self.con.execute("COMMIT")
            log(f"BATCH {batch_id} ({batch_type}) [{prev:%Y-%m-%d} → {cutoff:%Y-%m-%d}) OK trong {dur:.1f}s")
        except Exception:
            self.con.execute("ROLLBACK")
            self.con.execute("INSERT INTO etl.batch_log VALUES (?, ?, ?, ?, ?, ?, ?, 'FAILED', ?)",
                             [batch_id, batch_type, prev, cutoff, started, datetime.now(),
                              time.perf_counter() - t0, str(sys.exc_info()[1])[:500]])
            raise


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-reset", action="store_true", help="không xóa kho hiện có")
    ap.add_argument("--initial-only", action="store_true")
    ap.add_argument("--cutoff", help="chạy 1 batch incremental tới mốc YYYY-MM-DD")
    args = ap.parse_args()

    p = Pipeline()
    if not args.no_reset and not args.cutoff:
        p.reset()
    p.extract_and_clean()
    p.ensure_schema()

    if args.cutoff:
        p.run_batch(datetime.fromisoformat(args.cutoff), "INCREMENTAL")
    else:
        p.run_batch(INITIAL_CUTOFF, "INITIAL")
        if not args.initial_only:
            for c in month_starts(INITIAL_CUTOFF, FINAL_CUTOFF):
                p.run_batch(c, "INCREMENTAL")
    p.con.execute("CHECKPOINT")
    log("HOÀN TẤT.")


if __name__ == "__main__":
    main()
