"""
统一数据获取与指标计算模块
核心设计：
1. 符号解析层统一处理 BTC / BTCUSDT / BTC_USDT 等格式
2. 请求保护层强制最小请求间隔 + 指数退避重试
3. 状态明确返回：ok / rate_limited / not_found / error
4. 降级链路：Hyperliquid -> 币安现货 -> Gate.io现货（无条件兜底）
"""
import time, requests
import logging

log = logging.getLogger(__name__)

BINANCE_MIRROR = "https://data-api.binance.vision"
GATEIO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

# 周期毫秒映射表
INTERVAL_MS_MAP = {
    "1m": 60 * 1000, "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
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

def to_binance_spot(symbol: str) -> str:
    return normalize_symbol(symbol) + "USDT"

def to_gate_spot(symbol: str) -> str:
    return normalize_symbol(symbol) + "_USDT"

def to_hyperliquid_coin(symbol: str) -> str:
    return normalize_symbol(symbol)


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
        except requests.exceptions.HTTPError as e:
            if attempt == max_retries - 1:
                return False, "error"
            time.sleep(2 ** attempt)
        except Exception as e:
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

    universe = data.get("universe", [])
    result = set(a["name"].upper() for a in universe if "name" in a)
    _HL_UNIVERSE_CACHE = result
    _HL_UNIVERSE_TIME = now
    log.info(f"Hyperliquid 币种列表加载成功，共 {len(result)} 个币种")
    return result

def has_hyperliquid_contract(symbol: str) -> bool:
    return to_hyperliquid_coin(symbol) in get_hyperliquid_universe()


# ================= K线数据获取 =================
def fetch_binance_spot_klines(symbol, interval="30m", limit=150, start_ms=None, end_ms=None):
    binance_sym = to_binance_spot(symbol)
    url = f"{BINANCE_MIRROR}/api/v3/klines"
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

    seen = set()
    klines = []
    for item in all_klines:
        ts = int(item[0])
        if ts in seen: continue
        seen.add(ts)
        klines.append({"timestamp": ts, "open": float(item[1]), "high": float(item[2]), "low": float(item[3]), "close": float(item[4]), "volume": float(item[5])})
    klines.sort(key=lambda x: x["timestamp"])
    log.info(f"币安现货K线成功: {symbol}, 共{len(klines)}根")
    return klines, "ok"

def fetch_gateio_spot_klines(symbol, interval="30m", limit=150):
    gate_sym = to_gate_spot(symbol)
    params = {"currency_pair": gate_sym, "interval": interval, "limit": limit}
    ok, data = _request_with_retry(GATEIO_SPOT_URL, source="gate", params=params)
    if not ok: return None, data
    if not data: return None, "not_found"

    klines = [{"timestamp": int(item[0]) * 1000, "volume": float(item[1]), "close": float(item[2]), "high": float(item[3]), "low": float(item[4]), "open": float(item[5])} for item in data]
    log.info(f"Gate现货K线成功: {symbol}, 共{len(klines)}根")
    return klines, "ok"

def fetch_hyperliquid_klines(symbol, interval="30m", limit=150, start_ms=None, end_ms=None):
    coin = to_hyperliquid_coin(symbol)
    now_ms = int(time.time() * 1000)
    
    if start_ms and end_ms:
        start_t, end_t = start_ms, end_ms
    else:
        interval_ms = INTERVAL_MS_MAP.get(interval, 30 * 60 * 1000)
        start_t = now_ms - (limit * interval_ms)
        end_t = now_ms

    payload = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_t, "endTime": end_t}}
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST", source="hyperliquid", json=payload)
    if not ok: return None, data
    if not data or not isinstance(data, list): return None, "not_found"

    klines = [{"timestamp": item["t"], "open": float(item["o"]), "high": float(item["h"]), "low": float(item["l"]), "close": float(item["c"]), "volume": float(item["v"])} for item in data]
    log.info(f"Hyperliquid K线成功: {symbol}, 共{len(klines)}根")
    return klines, "ok"

def fetch_hyperliquid_metrics(symbol, current_price):
    coin = to_hyperliquid_coin(symbol)
    ok, data = _request_with_retry(HYPERLIQUID_INFO_URL, method="POST", source="hyperliquid", json={"type": "metaAndAssetCtxs"})
    if not ok or not data: return None
    try:
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin:
                ctx = ctxs[i] if i < len(ctxs) else {}
                oi_usd = float(ctx.get("openInterest", 0)) * current_price
                return {"funding_rate": float(ctx.get("funding", 0)), "open_interest": oi_usd, "day_volume": float(ctx.get("dayNtlVlm", 0))}
    except Exception: pass
    return None


# ================= 技术指标 =================
def calc_ma(klines, period=10):
    if len(klines) < period: return None
    return sum(k["close"] for k in klines[-period:]) / period

def calc_atr(klines, period=14):
    if len(klines) < period + 1: return None
    trs = [max(klines[i]["high"] - klines[i]["low"], abs(klines[i]["high"] - klines[i-1]["close"]), abs(klines[i]["low"] - klines[i-1]["close"])) for i in range(-period, 0)]
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

def find_recent_high(klines, lookback=149):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    return max(k["high"] for k in subset)

def find_recent_low(klines, lookback=149):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    return min(k["low"] for k in subset)


# ================= 构建 market_data =================
def build_market_data(symbol, asset_type):
    md = {"symbol": symbol, "asset_type": asset_type, "fetch_status": "ok", "data_mode": "futures", "klines": None, "klines_4h": None, "current_price": None, "funding_rate": None, "funding_percentile": None, "open_interest": None, "day_volume": None, "ma10": None, "atr": None, "rsi": None, "adx": None, "recent_high": None, "recent_low": None, "data_source": "Unknown"}

    if asset_type == "spot":
        klines, status = fetch_binance_spot_klines(symbol)
        source = "币安镜像 现货"
        if not klines:  # 🚀 无条件尝试下一个数据源
            klines, status = fetch_gateio_spot_klines(symbol)
            source = "Gate.io 现货"
        if klines:
            md.update(_build_indicators(klines))
            md.update({"data_source": source, "data_mode": "spot"})
            return md, "ok"
        md["fetch_status"] = status
        return md, status

    # 合约模式
    klines, status = fetch_hyperliquid_klines(symbol)
    if status == "rate_limited":
        md["fetch_status"] = "rate_limited"
        md["data_source"] = "Hyperliquid 限流"
        return md, "rate_limited"

    if not klines:
        log.warning(f"[{symbol}] Hyperliquid 无合约数据，降级到现货")
        
        # 🚀 第一步：尝试币安
        klines, status = fetch_binance_spot_klines(symbol)
        source = "币安镜像 现货（合约降级）"
        
        # 🚀 第二步：如果币安也失败（无论报什么错），无条件尝试 Gate.io
        if not klines:
            log.warning(f"[{symbol}] 币安无数据，继续降级尝试 Gate.io")
            klines, status = fetch_gateio_spot_klines(symbol)
            source = "Gate.io 现货（合约降级）"
            
        if klines:
            md.update(_build_indicators(klines))
            md.update({"data_source": source, "data_mode": "spot"})
            return md, "ok"
        md["fetch_status"] = status
        return md, status

    # 合约数据正常
    current_price = klines[-1]["close"]
    md.update(_build_indicators(klines))
    klines_4h_result, _ = fetch_hyperliquid_klines(symbol, interval="4h", limit=50)
    md["klines_4h"] = klines_4h_result
    md.update({"current_price": current_price, "data_source": "Hyperliquid 合约", "data_mode": "futures"})

    metrics = fetch_hyperliquid_metrics(symbol, current_price)
    if metrics:
        md["funding_rate"] = metrics["funding_rate"]
        md["open_interest"] = metrics["open_interest"]
        md["day_volume"] = metrics["day_volume"]
        fr = metrics["funding_rate"]
        if fr is not None:
            if fr <= -0.005: md["funding_percentile"] = -1.0
            elif fr <= 0: md["funding_percentile"] = fr / 0.01
            else: md["funding_percentile"] = min(1.0, fr / 0.01)
    return md, "ok"


def _build_indicators(klines):
    if not klines: return {}
    return {
        "klines": klines,
        "current_price": klines[-1]["close"],
        "ma10": calc_ma(klines, 10),
        "atr": calc_atr(klines, 14),
        "rsi": calc_rsi(klines, 14),
        "adx": calc_adx(klines, 14),
        "recent_high": find_recent_high(klines),
        "recent_low": find_recent_low(klines)
    }
