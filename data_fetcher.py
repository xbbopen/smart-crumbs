"""
统一数据获取与多周期指标计算模块
核心设计：
1. 符号解析层统一处理 BTC / BTCUSDT / BTC_USDT 等格式
2. 多层别名映射：Hyperliquid(k前缀) / Gate.io(千倍币转换)
3. 请求保护层：最小请求间隔 + 指数退避重试
4. 降级链路：Hyperliquid -> 币安现货 -> Gate.io现货
5. 资金费率单位修复：×100 转为百分数
6. 🚀 多周期架构：拉取30m+4h，本地聚合出1h和1d，减少请求量
"""
import time, requests
import logging

log = logging.getLogger(__name__)

BINANCE_MIRROR = "https://data-api.binance.vision"
GATEIO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

INTERVAL_MS_MAP = {
    "1m": 60 * 1000, "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

# ================= 别名映射表 =================
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

# ================= 全局缓存 =================
_HL_UNIVERSE_CACHE = None
_HL_UNIVERSE_TIME = 0
_HL_UNIVERSE_TTL = 3600

_LAST_REQUEST = {"binance": 0.0, "gate": 0.0, "hyperliquid": 0.0}
_MIN_INTERVAL = {"binance": 0.3, "gate": 0.3, "hyperliquid": 1.5}


# ================= 符号解析层 =================
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


# ================= 资金费率单位转换 =================
def _funding_to_percent(fr: float) -> float:
    if fr is None: return None
    return fr * 100

def _funding_to_percentile(fr_percent: float) -> float:
    if fr_percent is None: return None
    if fr_percent <= 0:
        return max(-1.0, fr_percent / 0.01)
    return min(1.0, fr_percent / 0.01)


# ================= 请求保护层 =================
def _wait_for(source: str):
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


# ================= Hyperliquid 全币种列表 =================
def get_hyperliquid_universe():
    global _HL_UNIVERSE_CACHE, _HL_UNIVERSE_TIME
    now = time.time()
    if _HL_UNIVERSE_CACHE is not None and (now - _HL_UNIVERSE_TIME) < _HL_UNIVERSE_TTL:
        return _HL_UNIVERSE_CACHE
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST", source="hyperliquid", json={"type": "meta"})
    if not ok or not isinstance(data, dict):
        return set()
    result = set(a["name"].upper() for a in data.get("universe", []) if "name" in a)
    _HL_UNIVERSE_CACHE = result
    _HL_UNIVERSE_TIME = now
    log.info(f"✅ Hyperliquid 币种列表加载成功，共 {len(result)} 个合约")
    return result

def has_hyperliquid_contract(symbol: str) -> bool:
    return to_hyperliquid_coin(symbol) in get_hyperliquid_universe()

def audit_watchlist(watchlist):
    universe = get_hyperliquid_universe()
    has_contract, no_contract = [], []
    for item in watchlist:
        sym = item["symbol"]
        (has_contract if to_hyperliquid_coin(sym) in universe else no_contract).append(sym)
    return has_contract, no_contract


# ================= K线数据获取 =================
def fetch_binance_spot_klines(symbol, interval="30m", limit=200, start_ms=None, end_ms=None):
    binance_sym = to_binance_spot(symbol)
    url = f"{BINANCE_MIRROR}/api/v3/klines"
    result = _fetch_binance_klines_inner(url, binance_sym, interval, limit, start_ms, end_ms)
    if result[0]:
        return result
    alt_sym = to_binance_spot_fallback(symbol)
    if alt_sym != binance_sym:
        log.info(f"币安现货 {binance_sym} 失败，尝试降级为 {alt_sym}")
        return _fetch_binance_klines_inner(url, alt_sym, interval, limit, start_ms, end_ms)
    return None, result[1]

def _fetch_binance_klines_inner(url, binance_sym, interval, limit, start_ms, end_ms):
    all_klines = []
    if start_ms and end_ms:
        cur = start_ms
        while cur < end_ms:
            params = {"symbol": binance_sym, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
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
    log.info(f"币安现货K线成功: {binance_sym}, 共{len(klines)}根")
    return klines, "ok"

def fetch_gateio_spot_klines(symbol, interval="30m", limit=200):
    gate_sym = to_gate_spot(symbol)
    result = _fetch_gate_klines_inner(gate_sym, interval, limit)
    if result[0]:
        return result
    alt_sym = to_gate_spot_fallback(symbol)
    if alt_sym != gate_sym:
        log.info(f"Gate.io {gate_sym} 失败，尝试降级为 {alt_sym}")
        return _fetch_gate_klines_inner(alt_sym, interval, limit)
    return None, result[1]

def _fetch_gate_klines_inner(gate_sym, interval, limit):
    params = {"currency_pair": gate_sym, "interval": interval, "limit": limit}
    ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
    if not ok: return None, data
    if not data: return None, "not_found"
    klines = [{"timestamp": int(item[0]) * 1000, "volume": float(item[1]), "close": float(item[2]),
               "high": float(item[3]), "low": float(item[4]), "open": float(item[5])} for item in data]
    log.info(f"Gate现货K线成功: {gate_sym}, 共{len(klines)}根")
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
    payload = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                  "startTime": start_t, "endTime": end_t}}
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST", source="hyperliquid", json=payload)
    if not ok: return None, data
    if not data or not isinstance(data, list): return None, "not_found"
    klines = [{"timestamp": item["t"], "open": float(item["o"]), "high": float(item["h"]),
               "low": float(item["l"]), "close": float(item["c"]), "volume": float(item["v"])} for item in data]
    log.info(f"Hyperliquid K线成功: {symbol} ({interval}), 共{len(klines)}根")
    return klines, "ok"

def fetch_hyperliquid_metrics(symbol, current_price):
    coin = to_hyperliquid_coin(symbol)
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST", source="hyperliquid",
                                    json={"type": "metaAndAssetCtxs"})
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
                        "open_interest": oi_usd, "day_volume": float(ctx.get("dayNtlVlm", 0))}
    except Exception: pass
    return None


# ================= 🚀 多周期聚合 =================
def aggregate_klines(klines, factor):
    """把连续 factor 根K线合并为1根（从末尾对齐，确保最新一根完整）"""
    if not klines or len(klines) < factor:
        return []
    result = []
    n = len(klines)
    start = n % factor
    for i in range(start, n - factor + 1, factor):
        chunk = klines[i:i + factor]
        result.append({
            "timestamp": chunk[0]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(k["high"] for k in chunk),
            "low": min(k["low"] for k in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(k["volume"] for k in chunk),
        })
    return result


# ================= 技术指标 =================
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
    """返回RSI序列（用于背离检测）"""
    if len(klines) < period + 1: return []
    series = []
    for i in range(period, len(klines)):
        window = klines[:i+1]
        r = calc_rsi(window, period)
        if r is not None:
            series.append(r)
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
    """返回 (DIF, DEA, MACD柱)"""
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

def calc_kdj(klines, period=9, k_period=3, d_period=3):
    """返回 (K, D, J)"""
    if len(klines) < period: return None, None, None
    k, d = 50.0, 50.0
    for i in range(period - 1, len(klines)):
        window = klines[i - period + 1:i + 1]
        high = max(x["high"] for x in window)
        low = min(x["low"] for x in window)
        close = klines[i]["close"]
        if high == low:
            rsv = 50.0
        else:
            rsv = (close - low) / (high - low) * 100
        k = (2/3) * k + (1/3) * rsv
        d = (2/3) * d + (1/3) * k
    j = 3 * k - 2 * d
    return k, d, j

def calc_boll(klines, period=20, mult=2):
    """返回 (上轨, 中轨, 下轨)"""
    if len(klines) < period: return None, None, None
    closes = [k["close"] for k in klines[-period:]]
    mid = sum(closes) / period
    variance = sum((c - mid) ** 2 for c in closes) / period
    std = variance ** 0.5
    return mid + mult * std, mid, mid - mult * std

def find_recent_high(klines, lookback=199):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    if not subset: return None
    return max(k["high"] for k in subset)

def find_recent_low(klines, lookback=199):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    if not subset: return None
    return min(k["low"] for k in subset)

def detect_rsi_divergence(klines, rsi_series, look=10):
    """
    检测RSI背离。
    返回 "bearish" / "bullish" / None
    """
    if len(klines) < look + 5 or len(rsi_series) < look + 5:
        return None
    # 最近look根的K线
    recent_prices = [k["close"] for k in klines[-look:]]
    recent_highs = [k["high"] for k in klines[-look:]]
    recent_lows = [k["low"] for k in klines[-look:]]
    recent_rsi = rsi_series[-look:]

    half = look // 2
    # 顶背离：后半段价格新高，RSI未新高
    price_high_recent = max(recent_highs[half:])
    price_high_prev = max(recent_highs[:half])
    rsi_high_recent = max(recent_rsi[half:])
    rsi_high_prev = max(recent_rsi[:half])

    if price_high_recent > price_high_prev and rsi_high_recent < rsi_high_prev - 3:
        return "bearish"

    # 底背离：后半段价格新低，RSI未新低
    price_low_recent = min(recent_lows[half:])
    price_low_prev = min(recent_lows[:half])
    rsi_low_recent = min(recent_rsi[half:])
    rsi_low_prev = min(recent_rsi[:half])

    if price_low_recent < price_low_prev and rsi_low_recent > rsi_low_prev + 3:
        return "bullish"

    return None


# ================= 构建多周期市场数据 =================
def build_market_data(symbol, asset_type):
    """
    返回 (md, status)。md 包含多周期数据：
    - klines_30m, klines_1h, klines_4h, klines_1d
    - 各周期的指标：RSI/EMA/MACD/KDJ/BOLL/ATR/ADX
    """
    md = {
        "symbol": symbol, "asset_type": asset_type, "fetch_status": "ok",
        "data_mode": "futures", "data_source": "Unknown",
        "current_price": None,
        "funding_rate": None, "funding_rate_raw": None, "funding_percentile": None,
        "open_interest": None, "day_volume": None,
        # 各周期K线
        "klines_30m": None, "klines_1h": None, "klines_4h": None, "klines_1d": None,
        # 30m指标
        "ma10": None, "atr": None, "rsi": None, "adx": None,
        "recent_high": None, "recent_low": None,
        # 1h指标
        "rsi_1h": None, "kdj_1h": None, "boll_1h": None, "macd_1h": None,
        "rsi_div_1h": None,
        # 4h指标
        "ema20_4h": None, "ema50_4h": None, "rsi_4h": None, "macd_4h": None,
        "trend_4h": None,
        # 1d指标
        "ema50_1d": None, "rsi_1d": None, "trend_1d": None,
    }

    # ============ 数据获取 ============
    klines_30m, klines_4h = None, None
    source = ""

    if asset_type == "spot":
        klines_30m, status = fetch_binance_spot_klines(symbol, "30m", 200)
        source = "币安镜像 现货"
        if not klines_30m:
            klines_30m, status = fetch_gateio_spot_klines(symbol, "30m", 200)
            source = "Gate.io 现货"
        if klines_30m:
            klines_4h = aggregate_klines(klines_30m, 8)  # 8根30m = 4h
            md["data_mode"] = "spot"
    else:
        # 合约
        coin = to_hyperliquid_coin(symbol)
        universe = get_hyperliquid_universe()
        if coin in universe:
            klines_30m, status = fetch_hyperliquid_klines(symbol, "30m", 200)
            if status == "rate_limited":
                md["fetch_status"] = "rate_limited"
                md["data_source"] = "Hyperliquid 限流"
                return md, "rate_limited"
            if klines_30m:
                klines_4h, _ = fetch_hyperliquid_klines(symbol, "4h", 320)
                source = "Hyperliquid 合约"
                md["data_mode"] = "futures"
            else:
                log.warning(f"[{symbol}] Hyperliquid 有合约但拉取失败，降级现货")
        else:
            log.info(f"[{symbol}] Hyperliquid 无此合约(coin={coin})，降级现货")

        if not klines_30m:
            # 降级现货
            klines_30m, status = fetch_binance_spot_klines(symbol, "30m", 200)
            source = "币安镜像 现货（合约降级）"
            if not klines_30m:
                klines_30m, status = fetch_gateio_spot_klines(symbol, "30m", 200)
                source = "Gate.io 现货（合约降级）"
            if klines_30m:
                klines_4h = aggregate_klines(klines_30m, 8)
                md["data_mode"] = "spot"

    if not klines_30m:
        md["fetch_status"] = status
        return md, status

    # ============ 多周期聚合 ============
    klines_1h = aggregate_klines(klines_30m, 2)   # 2根30m = 1h
    if not klines_4h:
        klines_4h = aggregate_klines(klines_30m, 8)  # 8根30m = 4h
    klines_1d = aggregate_klines(klines_4h, 6)    # 6根4h = 1d

    md["klines_30m"] = klines_30m
    md["klines_1h"] = klines_1h
    md["klines_4h"] = klines_4h
    md["klines_1d"] = klines_1d
    md["current_price"] = klines_30m[-1]["close"]
    md["data_source"] = source

    # ============ 30m指标 ============
    md["ma10"] = calc_ma(klines_30m, 10)
    md["atr"] = calc_atr(klines_30m, 14)
    md["rsi"] = calc_rsi(klines_30m, 14)
    md["adx"] = calc_adx(klines_30m, 14)
    md["recent_high"] = find_recent_high(klines_30m, 199)
    md["recent_low"] = find_recent_low(klines_30m, 199)

    # ============ 1h指标 ============
    if len(klines_1h) >= 20:
        md["rsi_1h"] = calc_rsi(klines_1h, 14)
        k, d, j = calc_kdj(klines_1h, 9)
        md["kdj_1h"] = {"k": k, "d": d, "j": j}
        boll_upper, boll_mid, boll_lower = calc_boll(klines_1h, 20)
        md["boll_1h"] = {"upper": boll_upper, "mid": boll_mid, "lower": boll_lower}
        dif, dea, hist = calc_macd(klines_1h)
        md["macd_1h"] = {"dif": dif, "dea": dea, "hist": hist}
        rsi_series = calc_rsi_series(klines_1h, 14)
        md["rsi_div_1h"] = detect_rsi_divergence(klines_1h, rsi_series)

    # ============ 4h指标 ============
    if len(klines_4h) >= 50:
        md["ema20_4h"] = calc_ema(klines_4h, 20)
        md["ema50_4h"] = calc_ema(klines_4h, 50)
        md["rsi_4h"] = calc_rsi(klines_4h, 14)
        dif, dea, hist = calc_macd(klines_4h)
        md["macd_4h"] = {"dif": dif, "dea": dea, "hist": hist}

    # ============ 1d指标 ============
    if len(klines_1d) >= 50:
        md["ema50_1d"] = calc_ema(klines_1d, 50)
        md["rsi_1d"] = calc_rsi(klines_1d, 14)

    # ============ 趋势判断 ============
    price = md["current_price"]
    # 4H趋势
    if md["ema20_4h"] and md["ema50_4h"]:
        if md["ema20_4h"] > md["ema50_4h"] and price > md["ema20_4h"]:
            md["trend_4h"] = "up"
        elif md["ema20_4h"] < md["ema50_4h"] and price < md["ema20_4h"]:
            md["trend_4h"] = "down"
        else:
            md["trend_4h"] = "neutral"
    # 1D趋势
    if md["ema50_1d"]:
        if price > md["ema50_1d"] * 1.005:
            md["trend_1d"] = "up"
        elif price < md["ema50_1d"] * 0.995:
            md["trend_1d"] = "down"
        else:
            md["trend_1d"] = "neutral"

    # ============ 合约指标 ============
    if md["data_mode"] == "futures":
        metrics = fetch_hyperliquid_metrics(symbol, price)
        if metrics:
            md["funding_rate"] = metrics["funding_rate"]
            md["funding_rate_raw"] = metrics["funding_rate_raw"]
            md["open_interest"] = metrics["open_interest"]
            md["day_volume"] = metrics["day_volume"]
            md["funding_percentile"] = _funding_to_percentile(metrics["funding_rate"])
            log.info(f"[{symbol}] 费率原始: {metrics['funding_rate_raw']:.8f} | 转换: {metrics['funding_rate']:.6f}% | 拥挤度: {md['funding_percentile']:.4f}")

    return md, "ok"
