"""
SQLite 数据库管理模块（带 source 字段）
- 主键: (symbol, interval, timestamp)
- source: 标记每条K线的数据来源
"""
import sqlite3
import os
import logging

log = logging.getLogger(__name__)
DB_PATH = "data/market.db"


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'unknown',
            PRIMARY KEY (symbol, interval, timestamp)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_int ON klines(symbol, interval, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON klines(source)")
    conn.commit()
    return conn


def get_last_timestamp(symbol, interval, source=None):
    """查询最后时间戳。指定 source 时只查该源。"""
    conn = get_conn()
    try:
        if source:
            cur = conn.execute(
                "SELECT MAX(timestamp) FROM klines WHERE symbol=? AND interval=? AND source=?",
                (symbol, interval, source))
        else:
            cur = conn.execute(
                "SELECT MAX(timestamp) FROM klines WHERE symbol=? AND interval=?",
                (symbol, interval))
        return cur.fetchone()[0]
    finally:
        conn.close()


def upsert_klines(symbol, interval, klines, source="unknown"):
    """批量写入（同时间戳覆盖，source也更新）"""
    if not klines:
        return 0
    conn = get_conn()
    try:
        rows = [(symbol, interval, k["timestamp"], k["open"], k["high"],
                 k["low"], k["close"], k["volume"], source) for k in klines]
        conn.executemany(
            "INSERT OR REPLACE INTO klines "
            "(symbol, interval, timestamp, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def load_klines(symbol, interval, limit=500, source=None):
    """读取最新 limit 根。source=None 时读混合数据。"""
    conn = get_conn()
    try:
        if source:
            cur = conn.execute(
                "SELECT timestamp, open, high, low, close, volume, source FROM klines "
                "WHERE symbol=? AND interval=? AND source=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, interval, source, limit))
        else:
            cur = conn.execute(
                "SELECT timestamp, open, high, low, close, volume, source FROM klines "
                "WHERE symbol=? AND interval=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, interval, limit))
        rows = cur.fetchall()
    finally:
        conn.close()
    rows = list(reversed(rows))
    return [{"timestamp": r[0], "open": r[1], "high": r[2],
             "low": r[3], "close": r[4], "volume": r[5], "source": r[6]}
            for r in rows]


def count_klines(symbol, interval, source=None):
    conn = get_conn()
    try:
        if source:
            cur = conn.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol=? AND interval=? AND source=?",
                (symbol, interval, source))
        else:
            cur = conn.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol=? AND interval=?",
                (symbol, interval))
        return cur.fetchone()[0]
    finally:
        conn.close()


def source_stats(symbol, interval):
    """统计各源数据量"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT source, COUNT(*) FROM klines WHERE symbol=? AND interval=? GROUP BY source",
            (symbol, interval))
        return dict(cur.fetchall())
    finally:
        conn.close()


def db_size_mb():
    if os.path.exists(DB_PATH):
        return os.path.getsize(DB_PATH) / 1024 / 1024
    return 0.0


def list_symbols():
    conn = get_conn()
    try:
        cur = conn.execute("SELECT DISTINCT symbol FROM klines ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

def vacuum_db():
    """
    压缩数据库，返回 (before_mb, after_mb)。
    消除多处内联 VACUUM 带来的代码重复。
    """
    before = db_size_mb()
    conn = get_conn()
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    after = db_size_mb()
    return before, after
