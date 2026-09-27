"""
统一数据获取与指标计算模块
- 合约：Hyperliquid（失败自动降级现货）
- 现货：币安镜像优先 -> Gate.io -> Hyperliquid（最后兜底）
"""
import time, requests
import logging

log = logging.getLogger(__name__)

BINANCE_MIRROR = "https://data-api.binance.vision"
GATEIO_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


# ================= 币安镜像（现货） =================
def _binance_symbol(symbol: str) -> str:
    return symbol.replace("_", "").upper()

def fetch_binance_spot_klines(symbol, interval="30m", limit=150, start_ms=None, end_ms=None):
    binance_sym = _binance_symbol(symbol)
    url = f"{BINANCE_MIRROR}/api/v3/klines"
    all_klines = []
    if start_ms and end_ms:
        cur = start_ms
        while cur < end_ms:
            params = {"symbol": binance_sym, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
            try:
                r = requests.get(url, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning(f"币安镜像请求失败: {e}")
                break
            if not data:
                break
            all_klines.extend(data)
            cur = int(data[-1][0]) + 1
            time.sleep(0.2)
    else:
        params = {"symbol": binance_sym, "interval": interval, "limit": limit}
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            all_klines = data if data else []
        except Exception as e:
            log.warning(f"币安镜像请求失败: {e}")
            return None
    if not all_klines:
        return None
    seen = set()
    klines = []
    for item in all_klines:
        ts = int(item[0])
        if ts in seen: continue
        seen.add(ts)
        klines.append({
            "timestamp": ts,
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5])
        })
    klines.sort(key=lambda x: x["timestamp"])
    log.info(f"币安镜像K线成功: {symbol}, 共{len(klines)}根")
    return klines


# ================= Gate.io（现货） =================
def fetch_gateio_spot_klines(symbol, interval="30m", limit=150):
    try:
        r = requests.get(GATEIO_SPOT_URL, params={"currency_pair": symbol, "interval": interval, "limit": limit}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data: return None
        klines = [{
            "timestamp": int(item[0]) * 1000,
            "volume": float(item[1]),
            "close": float(item[2]),
            "high": float(item[3]),
            "low": float(item[4]),
            "open": float(item[5])
        } for item in data]
        log.info(f"Gate.io现货K线成功: {symbol}, 共{len(klines)}根")
        return klines
    except Exception as e:
        log.warning(f"Gate.io现货请求失败: {e}")
        return None


# ================= Hyperliquid（合约） =================
def _hl_coin(symbol: str) -> str:
    return symbol.replace("_USDT", "").replace("-USDT", "").replace("USDT", "").upper()

def fetch_hyperliquid_klines(symbol, interval="30m", limit=150, start_ms=None, end_ms=None):
    coin = _hl_coin(symbol)
    now_ms = int(time.time() * 1000)
    if start_ms and end_ms:
        start_t, end_t = start_ms, end_ms
    else:
        start_t = now_ms - (limit * 30 * 60 * 1000)
        end_t = now_ms
    payload = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_t, "endTime": end_t}}
    try:
        r = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data or not isinstance(data, list): return None
        klines = [{
            "timestamp": item["t"],
            "open": float(item["o"]),
            "high": float(item["h"]),
            "low": float(item["l"]),
            "close": float(item["c"]),
            "volume": float(item["v"])
        } for item in data]
        log.info(f"HyperliquidK线成功: {symbol}, 共{len(klines)}根")
        return klines
    except Exception as e:
        log.warning(f"Hyperliquid请求失败: {e}")
        return None

def fetch_hyperliquid_metrics(symbol, current_price):
    coin = _hl_coin(symbol)
    try:
        data = requests.post(HYPERLIQUID_INFO_URL, json={"type": "metaAndAssetCtxs"}, timeout=15).json()
        if not data: return None
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin:
                ctx = ctxs[i] if i < len(ctxs) else {}
                oi_usd = float(ctx.get("openInterest", 0)) * current_price
                return {
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": oi_usd,
                    "day_volume": float(ctx.get("dayNtlVlm", 0))
                }
    except Exception:
        pass
    return None


# ================= 技术指标 =================
def calc_ma(klines, period=10):
    if len(klines) < period: return None
    return sum(k["close"] for k in klines[-period:]) / period

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


# ================= 构建 market_data（核心降级逻辑） =================
def build_market_data(symbol, asset_type):
    md = {
        "symbol": symbol, "asset_type": asset_type, "fetch_status": "ok",
        "klines": None, "klines_4h": None, "current_price": None,
        "funding_rate": None, "funding_percentile": None,
        "open_interest": None, "day_volume": None,
        "ma10": None, "atr": None, "rsi": None, "adx": None,
        "recent_high": None, "recent_low": None, "data_source": "Unknown"
    }

    if asset_type == "futures":
        klines = fetch_hyperliquid_klines(symbol)
        if klines:
            current_price = klines[-1]["close"]
            md.update({
                "klines": klines, "current_price": current_price,
                "klines_4h": fetch_hyperliquid_klines(symbol, interval="4h", limit=50),
                "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
                "rsi": calc_rsi(klines, 14), "adx": calc_adx(klines, 14),
                "recent_high": find_recent_high(klines), "recent_low": find_recent_low(klines),
                "data_source": "Hyperliquid 合约"
            })
            metrics = fetch_hyperliquid_metrics(symbol, current_price)
            if metrics:
                md["funding_rate"] = metrics["funding_rate"]
                md["open_interest"] = metrics["open_interest"]
                md["day_volume"] = metrics["day_volume"]
                if metrics["funding_rate"] is not None:
                    md["funding_percentile"] = min(1.0, max(0.0, metrics["funding_rate"] / 0.01))
            return md
        else:
            # 🚀 核心修复：合约拿不到数据，触发降级拿现货
            log.warning(f"[{symbol}] 合约数据获取失败，触发降级：尝试拿现货数据")
            klines = fetch_binance_spot_klines(symbol)
            source = "币安镜像 现货（合约降级）"
            if not klines:
                klines = fetch_gateio_spot_klines(symbol)
                source = "Gate.io 现货（合约降级）"
            if not klines:
                klines = fetch_hyperliquid_klines(symbol)  # 虽然叫hl，但当作最后兜底
                source = "Hyperliquid 现货（合约降级）"
            
            if klines:
                md.update({
                    "klines": klines, "current_price": klines[-1]["close"],
                    "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
                    "rsi": calc_rsi(klines, 14), "adx": calc_adx(klines, 14),
                    "recent_high": find_recent_high(klines), "recent_low": find_recent_low(klines),
                    "data_source": source
                })
                # 现货降级后，没有资金费率，保持 funding_rate 为 None 即可（策略已兼容）
                return md
            else:
                md["fetch_status"] = "unsupported"
                return md

    elif asset_type == "spot":
        klines = fetch_binance_spot_klines(symbol)
        source = "币安镜像 现货"
        if not klines:
            klines = fetch_gateio_spot_klines(symbol)
            source = "Gate.io 现货"
        if not klines:
            klines = fetch_hyperliquid_klines(symbol)
            source = "Hyperliquid 合约（现货兜底）"
        if klines:
            md.update({
                "klines": klines, "current_price": klines[-1]["close"],
                "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
                "rsi": calc_rsi(klines, 14), "adx": calc_adx(klines, 14),
                "recent_high": find_recent_high(klines), "recent_low": find_recent_low(klines),
                "data_source": source
            })
            return md
        md["fetch_status"] = "unsupported"
        return md
    else:
        md["fetch_status"] = "unsupported"
        return md
