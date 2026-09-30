# -*- coding: utf-8 -*-
"""
统一数据获取模块（DB优先 + 增量 + 源切换保护 + 从最近N根探测）
"""
import time, requests
import logging

from data_db import get_last_timestamp, upsert_klines, load_klines, count_klines

log = logging.getLogger(__name__)

BINANCE_MIRROR = "https://data-api.binance.vision"
GATEIO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

INTERVAL_MS_MAP = {
    "1m": 60 * 1000, "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

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
    if fr is None: return None
    return fr * 100

def _funding_to_percentile(fr_percent):
    if fr_percent is None: return None
    if fr_percent <= 0: return max(-1.0, fr_percent / 0.01)
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
            if attempt == max_retries - 1: return False, "error"
            time.sleep(2 ** attempt)
        except Exception:
            if attempt == max_retries - 1: return False, "error"
            time.sleep(2 ** attempt)
    return False, "rate_limited"


def get_hyperliquid_universe():
    global _HL_UNIVERSE_CACHE, _HL_UNIVERSE_TIME
    now = time.time()
    if _HL_UNIVERSE_CACHE is not None and (now - _HL_UNIVERSE_TIME) < _HL_UNIVERSE_TTL:
        return _HL_UNIVERSE_CACHE
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST",
                                    source="hyperliquid", json={"type": "meta"})
    if not ok or not isinstance(data, dict): return set()
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
    if result[0]: return result
    alt_sym = to_binance_spot_fallback(symbol)
    if alt_sym != binance_sym:
        return _fetch_binance_klines_inner(url, alt_sym, interval, limit, start_ms, end_ms)
    return None, result[1]

def _fetch_binance_klines_inner(url, binance_sym, interval, limit, start_ms, end_ms):
    all_klines = []
    if start_ms and end_ms:
        cur = start_ms
        for _ in range(3):
            if cur >= end_ms: break
            chunk_end = min(cur + 5000 * INTERVAL_MS_MAP.get(interval, 30*60*1000), end_ms)
            params = {"symbol": binance_sym, "interval": interval,
                      "startTime": cur, "endTime": chunk_end, "limit": 1000}
            ok, data = _request_with_retry(url, source="binance", params=params)
            if not ok: return None, data
            if not data: break
            all_klines.extend(data)
            cur = int(data[-1][0]) + 1
    else:
        params = {"symbol": binance_sym, "interval": interval, "limit": limit}
        ok, data = _request_with_retry(url, source="binance", params=params)
        if not ok: return None, data
        all_klines = data if data else []
    if not all_klines: return None, "not_found"
    seen, klines = set(), []
    for item in all_klines:
        ts = int(item[0])
        if ts in seen: continue
        seen.add(ts)
        klines.append({"timestamp": ts, "open": float(item[1]), "high": float(item[2]),
                       "low": float(item[3]), "close": float(item[4]), "volume": float(item[5])})
    klines.sort(key=lambda x: x["timestamp"])
    return klines, "ok"

def fetch_gateio_spot_klines(symbol, interval="30m", limit=200, start_ms=None, end_ms=None):
    gate_sym = to_gate_spot(symbol)
    params = {"currency_pair": gate_sym, "interval": interval, "limit": min(limit, 1000)}
    if start_ms: params["from"] = start_ms // 1000
    if end_ms: params["to"] = end_ms // 1000
    ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
    if not ok or not data:
        alt_sym = to_gate_spot_fallback(symbol)
        if alt_sym != gate_sym:
            params["currency_pair"] = alt_sym
            ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
        if not ok or not data: return None, "not_found"
    klines = [{"timestamp": int(item[0]) * 1000, "volume": float(item[1]),
               "close": float(item[2]), "high": float(item[3]),
               "low": float(item[4]), "open": float(item[5])} for item in data]
    return klines, "ok"

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
    if not ok: return None, data
    if not data or not isinstance(data, list): return None, "not_found"
    klines = [{"timestamp": item["t"], "open": float(item["o"]),
               "high": float(item["h"]), "low": float(item["l"]),
               "close": float(item["c"]), "volume": float(item["v"])} for item in data]
    return klines, "ok"

def fetch_hyperliquid_metrics(symbol, current_price):
    coin = to_hyperliquid_coin(symbol)
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST",
                                    source="hyperliquid", json={"type": "metaAndAssetCtxs"})
    if not ok or not data: return None
    try:
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin.upper():
                ctx = ctxs[i] if i < len(ctxs) else {}
                oi_usd = float(ctx.get("openInterest", 0)) * current_price
                raw_fr = ctx.get("funding", 0)
                fr_percent = _funding_to_percent(float(raw_fr))
                return {"funding_rate_raw": float(raw_fr), "funding_rate": fr_percent,
                        "open_interest": oi_usd,
                        "day_volume": float(ctx.get("dayNtlVlm", 0))}
    except Exception: pass
    return None


# ================= 🚀 从"最近N根"开始试探 =================
# 扩展探测粒度，覆盖 4H/1D 的 2000+ 目标
_PROBE_SIZES = [50, 100, 200, 300, 500, 1000, 2000, 3000]

def fetch_klines_from_now(fetch_func, symbol, interval, target_bars):
    interval_ms = INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
    now_ms = int(time.time() * 1000)
    all_klines = []

    for probe_size in _PROBE_SIZES:
        if len(all_klines) >= target_bars:
            break
        start_ms = now_ms - (probe_size * interval_ms)
        klines, status = fetch_func(symbol, interval, probe_size, start_ms, now_ms)
        if klines:
            all_klines = klines
            log.info(f"    [{symbol}][{interval}] 试探{probe_size}根 → 拿到{len(klines)}根")
            if len(klines) < probe_size:
                log.info(f"    [{symbol}][{interval}] 数据边界到达，最终{len(klines)}根")
                break
        else:
            log.info(f"    [{symbol}][{interval}] 试探{probe_size}根 → {status}")
            if probe_size == _PROBE_SIZES[0]:
                log.warning(f"    [{symbol}][{interval}] 连最近50根都拿不到，该源无此周期数据")
                break
    return all_klines[:target_bars]


# ================= 主源判定 =================
def get_primary_source(symbol, asset_type):
    if asset_type == "futures":
        coin = to_hyperliquid_coin(symbol)
        if coin in get_hyperliquid_universe():
            return "hyperliquid"
    return "binance_spot"


def fetch_and_cache_klines(symbol, asset_type, interval, desired_bars):
    """DB优先。瀑布式降级：主源 → 币安 → Gate"""
    interval_ms = INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
    now_ms = int(time.time() * 1000)

    primary_source = get_primary_source(symbol, asset_type)

    last_ts = get_last_timestamp(symbol, interval, source=None)

    actual_source = primary_source
    klines_new = []

    if last_ts:
        fetch_start = last_ts + interval_ms
        if fetch_start >= now_ms:
            log.info(f"[{symbol}][{interval}] 数据已最新")
        else:
            if primary_source == "hyperliquid":
                klines_new, _ = fetch_hyperliquid_klines(symbol, interval, 5000, fetch_start, now_ms)
                if klines_new: actual_source = "hyperliquid"
            if not klines_new:
                klines_new, _ = fetch_binance_spot_klines(symbol, interval, 5000, fetch_start, now_ms)
                if klines_new: actual_source = "binance_spot"
            if not klines_new:
                klines_new, _ = fetch_gateio_spot_klines(symbol, interval, 5000, fetch_start, now_ms)
                if klines_new: actual_source = "gate_spot"
    else:
        log.warning(f"[{symbol}][{interval}] DB无数据，从最近N根探测")
        if primary_source == "hyperliquid":
            klines_new = fetch_klines_from_now(fetch_hyperliquid_klines, symbol, interval, desired_bars)
            if klines_new: actual_source = "hyperliquid"
        if not klines_new:
            klines_new = fetch_klines_from_now(fetch_binance_spot_klines, symbol, interval, desired_bars)
            if klines_new: actual_source = "binance_spot"
        if not klines_new:
            klines_new = fetch_klines_from_now(fetch_gateio_spot_klines, symbol, interval, desired_bars)
            if klines_new: actual_source = "gate_spot"

    if klines_new:
        upsert_klines(symbol, interval, klines_new, source=actual_source)

    klines_all = load_klines(symbol, interval, desired_bars, source=None)
    if len(klines_all) >= 50:
        return klines_all, {
            "primary": primary_source, "actual": actual_source,
            "mixed": primary_source != actual_source,
            "primary_count": len(klines_all),
        }

    fallback = klines_new or []
    return fallback, {
        "primary": primary_source, "actual": actual_source,
        "mixed": False, "primary_count": len(fallback),
    }


# ================= 技术指标（不变） =================
def calc_ma(klines, period=10):
    if len(klines) < period: return None
    return sum(k["close"] for k in klines[-period:]) / period

def calc_ema(klines, period):
    if len(klines) < period: return None
    closes = [k["close"] for k in klines]
    k = 2.0 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_atr(klines, period=14):
    if len(klines) < period + 1: return None
    trs = [max(klines[i]["high"] - klines[i]["low"],
               abs(klines[i]["high"] - klines[i-1]["close"]),
               abs(klines[i]["low"] - klines[i-1]["close"]))
           for i in range(-period, 0)]
    return sum(trs) / period

def calc_rsi(klines, period=14):
    if len(klines) < period + 1: return None
    gains, losses = 0, 0
    for i in range(-period, 0):
        ch = klines[i]["close"] - klines[i-1]["close"]
        if ch >= 0: gains += ch
        else: losses += abs(ch)
    if losses == 0: return 100
    return 100 - (100 / (1 + (gains / losses)))

def calc_rsi_series(klines, period=14):
    if len(klines) < period + 1: return []
    series = []
    for i in range(period, len(klines)):
        r = calc_rsi(klines[:i+1], period)
        if r is not None: series.append(r)
    return series

def calc_adx(klines, period=14):
    if len(klines) < period + 1: return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(-period, 0):
        high, low = klines[i]["high"], klines[i]["low"]
        prev_high, prev_low, prev_close = klines[i-1]["high"], klines[i-1]["low"], klines[i-1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move, down_move = high - prev_high, prev_low - low
        trs.append(tr)
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
    atr = sum(trs) / period
    if atr == 0: return 0
    plus_di = 100 * (sum(plus_dm) / period) / atr
    minus_di = 100 * (sum(minus_dm) / period) / atr
    if (plus_di + minus_di) == 0: return 0
    return 100 * abs(plus_di - minus_di) / (plus_di + minus_di)

def calc_macd(klines, fast=12, slow=26, signal=9):
    if len(klines) < slow + signal: return None, None, None
    closes = [k["close"] for k in klines]
    def ema_series(values, period):
        if len(values) < period: return []
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
    if len(klines) < period: return None, None, None
    k, d = 50.0, 50.0
    for i in range(period - 1, len(klines)):
        window = klines[i - period + 1:i + 1]
        high = max(x["high"] for x in window)
        low = min(x["low"] for x in window)
        close = klines[i]["close"]
        rsv = 50.0 if high == low else (close - low) / (high - low) * 100
        k = (2/3) * k + (1/3) * rsv
        d = (2/3) * d + (1/3) * k
    return k, d, 3 * k - 2 * d

def calc_boll(klines, period=20, mult=2):
    if len(klines) < period: return None, None, None
    closes = [k["close"] for k in klines[-period:]]
    mid = sum(closes) / period
    variance = sum((c - mid) ** 2 for c in closes) / period
    std = variance ** 0.5
    return mid + mult * std, mid, mid - mult * std

def find_recent_high(klines, lookback=199):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    return max(k["high"] for k in subset) if subset else None

def find_recent_low(klines, lookback=199):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    return min(k["low"] for k in subset) if subset else None

def detect_rsi_divergence(klines, rsi_series, look=10):
    if len(klines) < look + 5 or len(rsi_series) < look + 5: return None
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
    if not klines or len(klines) < factor: return []
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

    # 🚀 1D K线：优先直接从数据源拉取，避免因 4H 数据不足导致日线指标全空
    klines_1d = []
    try:
        klines_1d_direct, _ = fetch_and_cache_klines(symbol, asset_type, "1d", 400)
        if klines_1d_direct:
            klines_1d = klines_1d_direct
            log.info(f"[{symbol}][1d] 直接拉取成功，共 {len(klines_1d)} 根")
    except Exception as e:
        log.warning(f"[{symbol}][1d] 直接拉取失败（将降级从4H聚合）：{type(e).__name__}: {e}")

    # 兜底：1D 数据不足 50 根时，从 4H 聚合
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

    if md["data_mode"] == "futures" and is_hl:
        metrics = fetch_hyperliquid_metrics(symbol, price)
        if metrics:
            md["funding_rate"] = metrics["funding_rate"]
            md["funding_rate_raw"] = metrics["funding_rate_raw"]
            md["open_interest"] = metrics["open_interest"]
            md["day_volume"] = metrics["day_volume"]
            md["funding_percentile"] = _funding_to_percentile(metrics["funding_rate"])

    return md, "ok"
