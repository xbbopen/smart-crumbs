# -*- coding: utf-8 -*-
"""
统一数据获取模块（DB优先 + 增量 + 源切换保护 + 智能探测 + 数据源缓存 + 衍生品指标）

v2 改动：
1. fetch_hyperliquid_metrics 增加 markPx / oraclePx / basis_pct
2. 新增 calc_vwap（滚动 VWAP）
3. build_market_data 中计算 vwap_30m、basis_pct、oi_change_pct_1h/4h
4. OI 历史写入 data_db，供跨运行计算变化率
"""
import time, requests, math
import logging

from data_db import (
    get_last_timestamp, upsert_klines, load_klines, count_klines,
    get_source_override, set_source_override, clear_source_override,
    get_max_available, set_max_available, clear_max_available,
    append_oi_history, get_oi_change,
)

log = logging.getLogger(__name__)

BINANCE_MIRROR = "https://data-api.binance.vision"
GATEIO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

INTERVAL_MS_MAP = {
    "1m": 60 * 1000, "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

_PROBE_SIZES = [500, 1000, 2000, 3000, 5000, 8000]

_HL_ALIAS = {
    "1000PEPE": "kPEPE", "1000BONK": "kBONK", "1000SHIB": "kSHIB",
    "1000FLOKI": "kFLOKI", "1000LUNC": "kLUNC", "1000DOGS": "kDOGS",
    "1000RATS": "kRATS", "1000SATS": "kSATS", "1000CAT": "kCAT",
    "1000MOG": "kMOG", "1000NEIRO": "kNEIRO", "1000X": "kX",
}
_GATE_ALIAS = {
    "1000PEPE": "PEPE", "1000BONK": "BONK", "1000SHIB": "SHIB",
    "1000FLOKI": "FLOKI", "1000LUNC": "LUNC", "1000DOGS": "DOGS",
    "1000RATS": "RATS", "1000SATS": "SATS", "1000CAT": "CAT",
    "1000MOG": "MOG", "1000NEIRO": "NEIRO",
}
_BINANCE_SPOT_ALIAS = {
    "1000PEPE": "PEPE", "1000BONK": "BONK", "1000SHIB": "SHIB",
    "1000FLOKI": "FLOKI", "1000LUNC": "LUNC",
}

_HL_UNIVERSE_CACHE = None
_HL_UNIVERSE_TIME = 0
_HL_UNIVERSE_TTL = 3600

_LAST_REQUEST = {"binance": 0.0, "gate": 0.0, "hyperliquid": 0.0}
_MIN_INTERVAL = {"binance": 0.3, "gate": 0.3, "hyperliquid": 1.5}


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("_USDT", "").replace("USDT", "").replace("_", "").strip()


def to_hyperliquid_coin(symbol: str) -> str:
    coin = normalize_symbol(symbol)
    return _HL_ALIAS.get(coin, coin)


def to_binance_spot(symbol: str) -> str:
    return normalize_symbol(symbol) + "USDT"


def to_binance_spot_fallback(symbol: str) -> str:
    coin = normalize_symbol(symbol)
    return _BINANCE_SPOT_ALIAS.get(coin, coin) + "USDT"


def to_gate_spot(symbol: str) -> str:
    return normalize_symbol(symbol) + "_USDT"


def to_gate_spot_fallback(symbol: str) -> str:
    coin = normalize_symbol(symbol)
    return _GATE_ALIAS.get(coin, coin) + "_USDT"


def _funding_to_percent(fr):
    if fr is None:
        return None
    return fr * 100


def _funding_to_percentile(fr_percent):
    if fr_percent is None:
        return None
    if fr_percent <= 0:
        return max(-1.0, fr_percent / 0.01)
    return min(1.0, fr_percent / 0.01)


def _wait_for(source):
    now = time.time()
    elapsed = now - _LAST_REQUEST[source]
    if elapsed < _MIN_INTERVAL[source]:
        time.sleep(_MIN_INTERVAL[source] - elapsed)
    _LAST_REQUEST[source] = time.time()


def _request_with_retry(url, method="GET", source="binance", max_retries=3, **kwargs):
    for attempt in range(max_retries):
        _wait_for(source)
        try:
            if method == "GET":
                r = requests.get(url, timeout=25, **kwargs)
            else:
                r = requests.post(url, timeout=25, **kwargs)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                log.warning(f"[{source}] 429 限流，第{attempt+1}次重试，等待{wait}秒")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return False, "not_found"
            r.raise_for_status()
            return True, r.json()
        except requests.exceptions.HTTPError:
            if attempt == max_retries - 1:
                return False, "error"
            time.sleep(2 ** attempt)
        except Exception:
            if attempt == max_retries - 1:
                return False, "error"
            time.sleep(2 ** attempt)
    return False, "rate_limited"


def get_hyperliquid_universe():
    global _HL_UNIVERSE_CACHE, _HL_UNIVERSE_TIME
    now = time.time()
    if _HL_UNIVERSE_CACHE is not None and (now - _HL_UNIVERSE_TIME) < _HL_UNIVERSE_TTL:
        return _HL_UNIVERSE_CACHE
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST",
                                   source="hyperliquid", json={"type": "meta"})
    if not ok or not isinstance(data, dict):
        return set()
    result = set(a["name"].upper() for a in data.get("universe", []) if "name" in a)
    _HL_UNIVERSE_CACHE = result
    _HL_UNIVERSE_TIME = now
    log.info(f"✅ Hyperliquid 币种列表加载成功，共 {len(result)} 个")
    return result


def has_hyperliquid_contract(symbol):
    return to_hyperliquid_coin(symbol) in get_hyperliquid_universe()


def audit_watchlist(watchlist):
    universe = get_hyperliquid_universe()
    has_contract, no_contract = [], []
    for item in watchlist:
        sym = item["symbol"]
        (has_contract if to_hyperliquid_coin(sym) in universe else no_contract).append(sym)
    return has_contract, no_contract


# ================= K线获取 =================
def fetch_binance_spot_klines(symbol, interval="30m", limit=200, start_ms=None, end_ms=None):
    binance_sym = to_binance_spot(symbol)
    url = f"{BINANCE_MIRROR}/api/v3/klines"
    result = _fetch_binance_klines_inner(url, binance_sym, interval, limit, start_ms, end_ms)
    if result[0]:
        return result
    alt_sym = to_binance_spot_fallback(symbol)
    if alt_sym != binance_sym:
        return _fetch_binance_klines_inner(url, alt_sym, interval, limit, start_ms, end_ms)
    return None, result[1]


def _fetch_binance_klines_inner(url, binance_sym, interval, limit, start_ms, end_ms):
    all_klines = []
    if start_ms and end_ms:
        cur = start_ms
        max_loops = math.ceil(limit / 1000) + 2
        for _ in range(max_loops):
            if cur >= end_ms:
                break
            chunk_end = min(cur + 1000 * INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000), end_ms)
            params = {"symbol": binance_sym, "interval": interval,
                      "startTime": cur, "endTime": chunk_end, "limit": 1000}
            ok, data = _request_with_retry(url, source="binance", params=params)
            if not ok:
                return None, data
            if not data:
                cur = chunk_end + INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
                continue
            all_klines.extend(data)
            cur = int(data[-1][0]) + 1
            if len(data) < 1000:
                break
    else:
        params = {"symbol": binance_sym, "interval": interval, "limit": min(limit, 1000)}
        ok, data = _request_with_retry(url, source="binance", params=params)
        if not ok:
            return None, data
        all_klines = data if data else []
    if not all_klines:
        return None, "not_found"
    seen, klines = set(), []
    for item in all_klines:
        ts = int(item[0])
        if ts in seen:
            continue
        seen.add(ts)
        klines.append({"timestamp": ts, "open": float(item[1]), "high": float(item[2]),
                       "low": float(item[3]), "close": float(item[4]), "volume": float(item[5])})
    klines.sort(key=lambda x: x["timestamp"])
    return klines, "ok"


def fetch_gateio_spot_klines(symbol, interval="30m", limit=200, start_ms=None, end_ms=None):
    gate_sym = to_gate_spot(symbol)
    all_klines = []

    if start_ms and end_ms:
        cur = start_ms
        max_loops = math.ceil(limit / 1000) + 2
        for _ in range(max_loops):
            if cur >= end_ms:
                break
            chunk_end = min(cur + 1000 * INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000), end_ms)
            params = {"currency_pair": gate_sym, "interval": interval, "limit": 1000,
                      "from": cur // 1000, "to": chunk_end // 1000}
            ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
            if not ok or not data:
                alt_sym = to_gate_spot_fallback(symbol)
                if alt_sym != gate_sym:
                    params["currency_pair"] = alt_sym
                    ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
            if not ok or not data:
                cur = chunk_end + INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
                continue
            parsed = [{"timestamp": int(item[0]) * 1000, "volume": float(item[1]),
                       "close": float(item[2]), "high": float(item[3]),
                       "low": float(item[4]), "open": float(item[5])} for item in data]
            all_klines.extend(parsed)
            cur = max(k["timestamp"] for k in parsed) + 1
            if len(data) < 1000:
                break
    else:
        params = {"currency_pair": gate_sym, "interval": interval, "limit": min(limit, 1000)}
        ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
        if not ok or not data:
            alt_sym = to_gate_spot_fallback(symbol)
            if alt_sym != gate_sym:
                params["currency_pair"] = alt_sym
                ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
            if not ok or not data:
                return None, "not_found"
        all_klines = [{"timestamp": int(item[0]) * 1000, "volume": float(item[1]),
                       "close": float(item[2]), "high": float(item[3]),
                       "low": float(item[4]), "open": float(item[5])} for item in data]

    seen, result = set(), []
    for k in sorted(all_klines, key=lambda x: x["timestamp"]):
        if k["timestamp"] not in seen:
            seen.add(k["timestamp"])
            result.append(k)
    return result, "ok"


def fetch_hyperliquid_klines(symbol, interval="30m", limit=200, start_ms=None, end_ms=None):
    coin = to_hyperliquid_coin(symbol)
    now_ms = int(time.time() * 1000)
    if start_ms and end_ms:
        start_t, end_t = start_ms, end_ms
    else:
        interval_ms = INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
        start_t = now_ms - (limit * interval_ms)
        end_t = now_ms
    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": interval,
                       "startTime": start_t, "endTime": end_t}}
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST",
                                   source="hyperliquid", json=payload)
    if not ok:
        return None, data
    if not data or not isinstance(data, list):
        return None, "not_found"
    klines = [{"timestamp": item["t"], "open": float(item["o"]),
               "high": float(item["h"]), "low": float(item["l"]),
               "close": float(item["c"]), "volume": float(item["v"])} for item in data]
    return klines, "ok"


def fetch_hyperliquid_metrics(symbol, current_price):
    """
    拉取 Hyperliquid 合约指标。
    返回 dict 包含：funding_rate_raw / funding_rate / open_interest /
    open_interest_coin / day_volume / mark_px / oracle_px / basis_pct
    """
    coin = to_hyperliquid_coin(symbol)
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST",
                                   source="hyperliquid", json={"type": "metaAndAssetCtxs"})
    if not ok or not data:
        return None
    try:
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin.upper():
                ctx = ctxs[i] if i < len(ctxs) else {}

                oi_coin = float(ctx.get("openInterest", 0) or 0)
                oi_usd = oi_coin * current_price

                raw_fr = ctx.get("funding", 0)
                fr_percent = _funding_to_percent(float(raw_fr))

                mark_px = None
                oracle_px = None
                basis_pct = None
                try:
                    if ctx.get("markPx"):
                        mark_px = float(ctx["markPx"])
                    if ctx.get("oraclePx"):
                        oracle_px = float(ctx["oraclePx"])
                    if mark_px and oracle_px and oracle_px > 0:
                        basis_pct = (mark_px - oracle_px) / oracle_px * 100
                except (TypeError, ValueError):
                    pass

                return {
                    "funding_rate_raw": float(raw_fr),
                    "funding_rate": fr_percent,
                    "open_interest": oi_usd,
                    "open_interest_coin": oi_coin,
                    "day_volume": float(ctx.get("dayNtlVlm", 0) or 0),
                    "mark_px": mark_px,
                    "oracle_px": oracle_px,
                    "basis_pct": basis_pct,
                }
    except Exception as e:
        log.warning(f"[hyperliquid_metrics] {symbol} 解析异常: {type(e).__name__}: {e}")
    return None


# ================= 智能探测拉取 =================
def fetch_klines_from_now(fetch_func, symbol, interval, target_bars):
    interval_ms = INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
    now_ms = int(time.time() * 1000)
    func_name = getattr(fetch_func, "__name__", "")

    if "hyperliquid" in func_name:
        start_ms = now_ms - (target_bars * interval_ms)
        klines, status = fetch_func(symbol, interval, target_bars, start_ms, now_ms)
        if klines:
            log.info(f"    [{symbol}][{interval}] Hyperliquid一击式请求{target_bars}根 → 拿到{len(klines)}根")
            return klines[:target_bars]
        log.warning(f"    [{symbol}][{interval}] Hyperliquid一击式请求失败: {status}")
        return []
    else:
        all_klines = []
        for probe_size in _PROBE_SIZES:
            if len(all_klines) >= target_bars:
                break
            start_ms = now_ms - (probe_size * interval_ms)
            klines, status = fetch_func(symbol, interval, probe_size, start_ms, now_ms)
            if klines:
                all_klines = klines
                log.info(f"    [{symbol}][{interval}] 探测{probe_size}根 → 拿到{len(klines)}根")
                if len(klines) < probe_size:
                    log.info(f"    [{symbol}][{interval}] 探测到上市边界，最终{len(klines)}根")
                    break
            else:
                log.info(f"    [{symbol}][{interval}] 探测{probe_size}根 → {status}")
                if probe_size == _PROBE_SIZES[0]:
                    log.warning(f"    [{symbol}][{interval}] 连最近500根都拿不到，该源无此周期数据")
                    break
        return all_klines[:target_bars]


# ================= 主源判定 =================
def get_primary_source(symbol, asset_type):
    if asset_type == "futures":
        coin = to_hyperliquid_coin(symbol)
        if coin in get_hyperliquid_universe():
            return "hyperliquid"
    return "binance_spot"


def _fetch_from_source_full(source_name, symbol, interval, target_bars):
    if source_name == "hyperliquid":
        return fetch_klines_from_now(fetch_hyperliquid_klines, symbol, interval, target_bars)
    elif source_name == "binance_spot":
        return fetch_klines_from_now(fetch_binance_spot_klines, symbol, interval, target_bars)
    elif source_name == "gate_spot":
        return fetch_klines_from_now(fetch_gateio_spot_klines, symbol, interval, target_bars)
    return []


def _fetch_from_source_range(source_name, symbol, interval, limit, start_ms, end_ms):
    if source_name == "hyperliquid":
        klines, _ = fetch_hyperliquid_klines(symbol, interval, limit, start_ms, end_ms)
        return klines
    elif source_name == "binance_spot":
        klines, _ = fetch_binance_spot_klines(symbol, interval, limit, start_ms, end_ms)
        return klines
    elif source_name == "gate_spot":
        klines, _ = fetch_gateio_spot_klines(symbol, interval, limit, start_ms, end_ms)
        return klines
    return []


# ============================================================
# 核心：fetch_and_cache_klines（带数据源 override + Hyperliquid 上线感知）
# ============================================================
def fetch_and_cache_klines(symbol, asset_type, interval, desired_bars):
    interval_ms = INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
    now_ms = int(time.time() * 1000)
    primary_source = get_primary_source(symbol, asset_type)

    last_ts = get_last_timestamp(symbol, interval, source=None)
    existing_count = count_klines(symbol, interval) if last_ts else 0

    override = get_source_override(symbol, interval)

    current_primary = get_primary_source(symbol, asset_type)
    if current_primary == "hyperliquid" and override and override != "hyperliquid":
        log.info(f"    [{symbol}][{interval}] ⚡ Hyperliquid 现已上线该合约，作废旧 override ({override})")
        clear_source_override(symbol, interval)
        clear_max_available(symbol, interval)
        override = None

    max_available = get_max_available(symbol, interval)

    if max_available and max_available > 0:
        effective_target = min(desired_bars, max_available)
    else:
        effective_target = desired_bars

    is_full_fetch = (not last_ts) or (existing_count < effective_target)

    if not is_full_fetch and max_available and existing_count >= max_available:
        log.info(f"[{symbol}][{interval}] 数据已就绪（{existing_count}/{max_available}，已到该币上限）")
    elif not is_full_fetch:
        log.info(f"[{symbol}][{interval}] 数据已最新（{existing_count}/{effective_target}）")

    sources_to_try = []
    if override:
        sources_to_try.append(override)
        log.info(f"    [{symbol}][{interval}] 使用缓存源: {override}")

    default_order = []
    if primary_source == "hyperliquid":
        default_order.append("hyperliquid")
    default_order.extend(["binance_spot", "gate_spot"])

    for s in default_order:
        if s not in sources_to_try:
            sources_to_try.append(s)

    actual_source = None
    klines_new = []

    if is_full_fetch:
        log.warning(f"[{symbol}][{interval}] DB数据不足({existing_count}/{effective_target})，重新全量拉取")
        for src in sources_to_try:
            klines_new = _fetch_from_source_full(src, symbol, interval, effective_target)
            if klines_new:
                actual_source = src
                set_source_override(symbol, interval, src)
                if len(klines_new) < desired_bars:
                    set_max_available(symbol, interval, len(klines_new))
                    log.info(f"    [{symbol}][{interval}] ✅ 使用 {src} 成功，拿到 {len(klines_new)} 根（已记录该币上限）")
                else:
                    clear_max_available(symbol, interval)
                    log.info(f"    [{symbol}][{interval}] ✅ 使用 {src} 成功，拉满 {len(klines_new)} 根")
                break
        if not actual_source:
            clear_source_override(symbol, interval)
            log.warning(f"    [{symbol}][{interval}] 所有源都失败，清除 override")
    else:
        fetch_start = last_ts + interval_ms
        if fetch_start >= now_ms:
            pass
        else:
            for src in sources_to_try:
                klines_new = _fetch_from_source_range(src, symbol, interval, 5000, fetch_start, now_ms)
                if klines_new:
                    actual_source = src
                    set_source_override(symbol, interval, src)
                    new_total = existing_count + len(klines_new)
                    if max_available and new_total > max_available:
                        set_max_available(symbol, interval, new_total)
                    break

    if klines_new:
        upsert_klines(symbol, interval, klines_new, source=actual_source or "unknown")

    klines_all = load_klines(symbol, interval, desired_bars, source=None)
    if len(klines_all) >= 50:
        return klines_all, {
            "primary": primary_source, "actual": actual_source or "cached",
            "mixed": primary_source != (actual_source or "cached"),
            "primary_count": len(klines_all),
        }

    fallback = klines_new or []
    return fallback, {
        "primary": primary_source, "actual": actual_source or "unknown",
        "mixed": False, "primary_count": len(fallback),
    }


# ================= 技术指标（Wilder 平滑版） =================
def calc_ma(klines, period=10):
    if len(klines) < period:
        return None
    return sum(k["close"] for k in klines[-period:]) / period


def calc_ema(klines, period):
    if len(klines) < period:
        return None
    closes = [k["close"] for k in klines]
    k = 2.0 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema


def calc_atr(klines, period=14):
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_close = klines[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def calc_rsi(klines, period=14):
    if len(klines) < period + 1:
        return None
    closes = [k["close"] for k in klines]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def calc_rsi_series(klines, period=14):
    if len(klines) < period + 1:
        return []
    closes = [k["close"] for k in klines]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_values = []
    if avg_loss == 0.0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - 100.0 / (1.0 + rs))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0.0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - 100.0 / (1.0 + rs))
    return rsi_values


def calc_adx(klines, period=14):
    if len(klines) < period * 2 + 1:
        return None
    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_high = klines[i - 1]["high"]
        prev_low = klines[i - 1]["low"]
        prev_close = klines[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    smoothed_tr = sum(trs[:period])
    smoothed_plus_dm = sum(plus_dms[:period])
    smoothed_minus_dm = sum(minus_dms[:period])

    dx_values = []
    for i in range(period, len(trs)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + trs[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dms[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dms[i]

        if smoothed_tr == 0:
            plus_di = 0.0
            minus_di = 0.0
        else:
            plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
            minus_di = 100.0 * smoothed_minus_dm / smoothed_tr

        if (plus_di + minus_di) == 0:
            dx = 0.0
        else:
            dx = 100.0 * abs(plus_di - minus_di) / (plus_di + minus_di)
        dx_values.append(dx)

    if len(dx_values) < period:
        return None
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period
    return adx


def calc_macd(klines, fast=12, slow=26, signal=9):
    if len(klines) < slow + signal:
        return None, None, None
    closes = [k["close"] for k in klines]

    def ema_series(values, period):
        if len(values) < period:
            return []
        k = 2.0 / (period + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    dif = [f - s for f, s in zip(ema_fast[-len(ema_slow):], ema_slow)]
    dea = ema_series(dif, signal)
    macd_hist = [(d - e) * 2 for d, e in zip(dif[-len(dea):], dea)]
    return dif[-1], dea[-1], macd_hist[-1]


def calc_kdj(klines, period=9):
    if len(klines) < period:
        return None, None, None
    k, d = 50.0, 50.0
    for i in range(period - 1, len(klines)):
        window = klines[i - period + 1:i + 1]
        high = max(x["high"] for x in window)
        low = min(x["low"] for x in window)
        close = klines[i]["close"]
        rsv = 50.0 if high == low else (close - low) / (high - low) * 100
        k = (2 / 3) * k + (1 / 3) * rsv
        d = (2 / 3) * d + (1 / 3) * k
    return k, d, 3 * k - 2 * d


def calc_boll(klines, period=20, mult=2):
    if len(klines) < period:
        return None, None, None
    closes = [k["close"] for k in klines[-period:]]
    mid = sum(closes) / period
    variance = sum((c - mid) ** 2 for c in closes) / period
    std = variance ** 0.5
    return mid + mult * std, mid, mid - mult * std


def calc_vwap(klines, period=48):
    """
    滚动 VWAP。默认 48 根 30m = 24 小时。
    使用典型价格 (H+L+C)/3 加权。
    """
    if not klines or len(klines) < 2:
        return None
    window = klines[-period:] if len(klines) >= period else klines
    typical_sum = 0.0
    vol_sum = 0.0
    for k in window:
        typical = (k["high"] + k["low"] + k["close"]) / 3.0
        typical_sum += typical * k["volume"]
        vol_sum += k["volume"]
    if vol_sum <= 0:
        return None
    return typical_sum / vol_sum


def find_recent_high(klines, lookback=199):
    subset = klines[-lookback - 1:-1] if len(klines) > lookback + 1 else klines[:-1]
    return max(k["high"] for k in subset) if subset else None


def find_recent_low(klines, lookback=199):
    subset = klines[-lookback - 1:-1] if len(klines) > lookback + 1 else klines[:-1]
    return min(k["low"] for k in subset) if subset else None


def detect_rsi_divergence(klines, rsi_series, look=10):
    if len(klines) < look + 5 or len(rsi_series) < look + 5:
        return None
    recent_highs = [k["high"] for k in klines[-look:]]
    recent_lows = [k["low"] for k in klines[-look:]]
    recent_rsi = rsi_series[-look:]
    half = look // 2
    if max(recent_highs[half:]) > max(recent_highs[:half]) and max(recent_rsi[half:]) < max(recent_rsi[:half]) - 3:
        return "bearish"
    if min(recent_lows[half:]) < min(recent_lows[:half]) and min(recent_rsi[half:]) > min(recent_rsi[:half]) + 3:
        return "bullish"
    return None


def aggregate_klines(klines, factor):
    if not klines or len(klines) < factor:
        return []
    result = []
    n = len(klines)
    start = n % factor
    for i in range(start, n - factor + 1, factor):
        chunk = klines[i:i + factor]
        result.append({"timestamp": chunk[0]["timestamp"], "open": chunk[0]["open"],
                       "high": max(k["high"] for k in chunk),
                       "low": min(k["low"] for k in chunk),
                       "close": chunk[-1]["close"],
                       "volume": sum(k["volume"] for k in chunk)})
    return result


# ================= 构建多周期市场数据 =================
def build_market_data(symbol, asset_type):
    md = {
        "symbol": symbol, "asset_type": asset_type, "fetch_status": "ok",
        "data_mode": "futures", "data_source": "Unknown",
        "source_mixed": False, "primary_source": "unknown",
        "current_price": None,
        "funding_rate": None, "funding_rate_raw": None, "funding_percentile": None,
        "open_interest": None, "day_volume": None,
        "klines_30m": None, "klines_1h": None, "klines_4h": None, "klines_1d": None,
        "ma10": None, "atr": None, "rsi": None, "adx": None,
        "recent_high": None, "recent_low": None,
        "rsi_1h": None, "kdj_1h": None, "boll_1h": None, "macd_1h": None, "rsi_div_1h": None,
        "ema20_4h": None, "ema50_4h": None, "rsi_4h": None, "macd_4h": None, "trend_4h": None,
        "ema50_1d": None, "rsi_1d": None, "trend_1d": None,
        # 🚀 新增衍生品字段
        "vwap_30m": None,
        "basis_pct": None,
        "oi_change_pct_1h": None,
        "oi_change_pct_4h": None,
    }

    is_hl = False
    if asset_type == "futures":
        coin = to_hyperliquid_coin(symbol)
        is_hl = coin in get_hyperliquid_universe()

    if is_hl:
        klines_30m, info_30m = fetch_and_cache_klines(symbol, "futures", "30m", 500)
        klines_4h, info_4h = fetch_and_cache_klines(symbol, "futures", "4h", 2000)
        md["data_mode"] = "futures"
        base_source = "Hyperliquid 合约"
    else:
        klines_30m, info_30m = fetch_and_cache_klines(symbol, "spot", "30m", 500)
        klines_4h, info_4h = fetch_and_cache_klines(symbol, "spot", "4h", 2000)
        md["data_mode"] = "spot"
        base_source = "现货（合约降级或原生现货）"

    if not klines_30m:
        md["fetch_status"] = "unsupported"
        return md, "unsupported"

    md["primary_source"] = info_30m["primary"]
    md["source_mixed"] = info_30m["mixed"] or info_4h["mixed"]
    md["data_source"] = base_source

    klines_1h = aggregate_klines(klines_30m, 2)
    if not klines_4h:
        klines_4h = aggregate_klines(klines_30m, 8)

    klines_1d = []
    try:
        klines_1d_direct, _ = fetch_and_cache_klines(symbol, asset_type, "1d", 400)
        if klines_1d_direct:
            klines_1d = klines_1d_direct
            log.info(f"[{symbol}][1d] 数据就绪，共 {len(klines_1d)} 根")
    except Exception as e:
        log.warning(f"[{symbol}][1d] 拉取失败（将降级从4H聚合）：{type(e).__name__}: {e}")

    if len(klines_1d) < 50:
        klines_1d_agg = aggregate_klines(klines_4h, 6)
        if len(klines_1d_agg) > len(klines_1d):
            klines_1d = klines_1d_agg
            log.info(f"[{symbol}][1d] 使用4H聚合兜底，共 {len(klines_1d)} 根")

    md["klines_30m"] = klines_30m
    md["klines_1h"] = klines_1h
    md["klines_4h"] = klines_4h
    md["klines_1d"] = klines_1d
    md["current_price"] = klines_30m[-1]["close"]

    md["ma10"] = calc_ma(klines_30m, 10)
    md["atr"] = calc_atr(klines_30m, 14)
    md["rsi"] = calc_rsi(klines_30m, 14)
    md["adx"] = calc_adx(klines_30m, 14)
    md["recent_high"] = find_recent_high(klines_30m, 199)
    md["recent_low"] = find_recent_low(klines_30m, 199)

    # 🚀 VWAP
    md["vwap_30m"] = calc_vwap(klines_30m, 48)

    if len(klines_1h) >= 20:
        md["rsi_1h"] = calc_rsi(klines_1h, 14)
        k, d, j = calc_kdj(klines_1h, 9)
        md["kdj_1h"] = {"k": k, "d": d, "j": j}
        _, mid, _ = calc_boll(klines_1h, 20)
        md["boll_1h"] = {"mid": mid}
        dif, dea, hist = calc_macd(klines_1h)
        md["macd_1h"] = {"dif": dif, "dea": dea, "hist": hist}
        rsi_series = calc_rsi_series(klines_1h, 14)
        md["rsi_div_1h"] = detect_rsi_divergence(klines_1h, rsi_series)

    if len(klines_4h) >= 50:
        md["ema20_4h"] = calc_ema(klines_4h, 20)
        md["ema50_4h"] = calc_ema(klines_4h, 50)
        md["rsi_4h"] = calc_rsi(klines_4h, 14)
        dif, dea, hist = calc_macd(klines_4h)
        md["macd_4h"] = {"dif": dif, "dea": dea, "hist": hist}

    if len(klines_1d) >= 50:
        md["ema50_1d"] = calc_ema(klines_1d, 50)
        md["rsi_1d"] = calc_rsi(klines_1d, 14)

    price = md["current_price"]
    if md["ema20_4h"] and md["ema50_4h"]:
        if md["ema20_4h"] > md["ema50_4h"] and price > md["ema20_4h"]:
            md["trend_4h"] = "up"
        elif md["ema20_4h"] < md["ema50_4h"] and price < md["ema20_4h"]:
            md["trend_4h"] = "down"
        else:
            md["trend_4h"] = "neutral"
    if md["ema50_1d"]:
        if price > md["ema50_1d"] * 1.005:
            md["trend_1d"] = "up"
        elif price < md["ema50_1d"] * 0.995:
            md["trend_1d"] = "down"
        else:
            md["trend_1d"] = "neutral"

    # 🚀 衍生品指标（OI / 基差 / 费率）
    if md["data_mode"] == "futures" and is_hl:
        metrics = fetch_hyperliquid_metrics(symbol, price)
        if metrics:
            md["funding_rate"] = metrics["funding_rate"]
            md["funding_rate_raw"] = metrics["funding_rate_raw"]
            md["open_interest"] = metrics["open_interest"]
            md["day_volume"] = metrics["day_volume"]
            md["funding_percentile"] = _funding_to_percentile(metrics["funding_rate"])
            md["basis_pct"] = metrics.get("basis_pct")

            # OI 历史 + 变化率
            oi_coin = metrics.get("open_interest_coin")
            if oi_coin and oi_coin > 0:
                try:
                    append_oi_history(symbol, "30m", oi_coin)
                    md["oi_change_pct_1h"] = get_oi_change(symbol, "30m", lookback_minutes=60)
                    md["oi_change_pct_4h"] = get_oi_change(symbol, "30m", lookback_minutes=240)
                except Exception as e:
                    log.warning(f"[{symbol}] OI 历史处理异常: {type(e).__name__}: {e}")

    return md, "ok"
