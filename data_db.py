# -*- coding: utf-8 -*-
"""
SQLite 数据库管理模块
- klines: K线数据
- meta: 通用键值表（数据源 override + max_available 缓存 + OI 历史）
"""
import sqlite3
import os
import json
import time
import logging

log = logging.getLogger(__name__)
DB_PATH = "data/market.db"

SOURCE_OVERRIDE_TTL = 30 * 24 * 3600
MAX_AVAILABLE_TTL = 30 * 24 * 3600

# OI 历史保留的最大条数（30m 频率下 500 条 ≈ 10 天）
OI_HISTORY_MAX_LEN = 500


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


# ============================================================
# meta 表基础读写
# ============================================================
def get_meta(key, default=None):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default
    except Exception as e:
        log.error(f"[meta] get_meta({key}) 失败: {e}")
        return default
    finally:
        conn.close()


def set_meta(key, value):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, str(value))
        )
        conn.commit()
    except Exception as e:
        log.error(f"[meta] set_meta({key}) 失败: {e}")
    finally:
        conn.close()


def delete_meta(key):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM meta WHERE key=?", (key,))
        conn.commit()
    except Exception as e:
        log.error(f"[meta] delete_meta({key}) 失败: {e}")
    finally:
        conn.close()


# ============================================================
# 数据源 override
# ============================================================
def _source_override_key(symbol, interval):
    return f"source_override:{symbol}:{interval}"


def get_source_override(symbol, interval):
    key = _source_override_key(symbol, interval)
    raw = get_meta(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if time.time() > data.get("expire_ts", 0):
            delete_meta(key)
            return None
        return data.get("source")
    except Exception:
        delete_meta(key)
        return None


def set_source_override(symbol, interval, source):
    key = _source_override_key(symbol, interval)
    data = {
        "source": source,
        "expire_ts": time.time() + SOURCE_OVERRIDE_TTL,
    }
    set_meta(key, json.dumps(data))


def clear_source_override(symbol, interval):
    delete_meta(_source_override_key(symbol, interval))


# ============================================================
# max_available（该标的能拿到的最大根数）
# ============================================================
def _max_available_key(symbol, interval):
    return f"max_available:{symbol}:{interval}"


def get_max_available(symbol, interval):
    key = _max_available_key(symbol, interval)
    raw = get_meta(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if time.time() > data.get("expire_ts", 0):
            delete_meta(key)
            return None
        return data.get("max_bars")
    except Exception:
        delete_meta(key)
        return None


def set_max_available(symbol, interval, max_bars):
    key = _max_available_key(symbol, interval)
    data = {
        "max_bars": int(max_bars),
        "expire_ts": time.time() + MAX_AVAILABLE_TTL,
    }
    set_meta(key, json.dumps(data))


def clear_max_available(symbol, interval):
    delete_meta(_max_available_key(symbol, interval))


# ============================================================
# 🚀 新增：OI 历史（用于计算 OI 变化率）
# ============================================================
def _oi_history_key(symbol, interval):
    return f"oi_history:{symbol}:{interval}"


def append_oi_history(symbol, interval, oi_value, max_len=OI_HISTORY_MAX_LEN):
    """
    追加一条 OI 记录。oi_value 为币本位 OI（coin 数量），
    也可以传 USD 计价 OI，只要口径保持一致即可。
    """
    if oi_value is None:
        return
    try:
        oi_value = float(oi_value)
    except (TypeError, ValueError):
        return
    if oi_value <= 0:
        return

    key = _oi_history_key(symbol, interval)
    raw = get_meta(key)
    history = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                history = parsed
        except Exception:
            history = []

    now_ms = int(time.time() * 1000)
    # 避免同一时间戳重复写入
    if history and history[-1].get("ts", 0) == now_ms:
        history[-1]["oi"] = oi_value
    else:
        history.append({"ts": now_ms, "oi": oi_value})

    history = history[-max_len:]
    set_meta(key, json.dumps(history))


def get_oi_change(symbol, interval="30m", lookback_minutes=60):
    """
    获取 OI 相对于 lookback_minutes 分钟前的变化百分比。
    返回 float 或 None（数据不足时）。
    """
    key = _oi_history_key(symbol, interval)
    raw = get_meta(key)
    if not raw:
        return None
    try:
        history = json.loads(raw)
    except Exception:
        return None
    if not isinstance(history, list) or len(history) < 2:
        return None

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - lookback_minutes * 60 * 1000

    old = None
    for h in history:
        if h.get("ts", 0) <= cutoff_ms:
            old = h
        else:
            break
    if old is None:
        old = history[0]

    cur = history[-1]
    if not old or not cur:
        return None
    old_oi = old.get("oi", 0)
    cur_oi = cur.get("oi", 0)
    if old_oi <= 0:
        return None
    return (cur_oi - old_oi) / old_oi * 100


def get_oi_history(symbol, interval="30m"):
    """调试用：返回完整 OI 历史列表。"""
    key = _oi_history_key(symbol, interval)
    raw = get_meta(key)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


# ============================================================
# K线读写
# ============================================================
def get_last_timestamp(symbol, interval, source=None):
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
    before = db_size_mb()
    conn = get_conn()
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    after = db_size_mb()
    return before, after
