import json
import os
import time
import sys
import logging
from datetime import datetime, timezone, timedelta

import requests

from email_sender import send_html_email
from strategies.loader import load_strategy

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
GATEIO_SPOT_KLINE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
BINANCE_ALPHA_TOKEN_LIST = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"

BJT = timezone(timedelta(hours=8))

def hyperliquid_post(payload: dict) -> dict:
    try:
        resp = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=15, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Hyperliquid API 请求失败: {e}")
        return None

def fetch_hyperliquid_klines(symbol: str, interval: str = "30m", limit: int = 100):
    coin = symbol.replace("_USDT", "").replace("-USDT", "").replace("USDT", "")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (limit * 30 * 60 * 1000)
    payload = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms}}
    data = hyperliquid_post(payload)
    if not data or not isinstance(data, list):
        return None
    klines = []
    for item in data:
        klines.append({"timestamp": item["t"], "open": float(item["o"]), "high": float(item["h"]), "low": float(item["l"]), "close": float(item["c"]), "volume": float(item["v"])})
    log.info(f"Hyperliquid K线获取成功: {symbol}, 共 {len(klines)} 根")
    return klines

def fetch_hyperliquid_funding(symbol: str):
    coin = symbol.replace("_USDT", "").replace("-USDT", "").replace("USDT", "")
    data = hyperliquid_post({"type": "metaAndAssetCtxs"})
    if not data:
        return None
    try:
        meta, ctxs = data[0], data[1]
        universe = meta.get("universe", [])
        for i, asset in enumerate(universe):
            if asset.get("name", "").upper() == coin.upper():
                ctx = ctxs[i] if i < len(ctxs) else {}
                return {"funding_rate": float(ctx.get("funding", 0)), "open_interest": float(ctx.get("openInterest", 0)), "mark_price": float(ctx.get("markPx", 0))}
    except (IndexError, KeyError, TypeError) as e:
        log.error(f"Hyperliquid 资金费率解析失败: {e}")
    return None

def fetch_gateio_spot_klines(symbol: str, interval: str = "30m", limit: int = 100):
    try:
        resp = requests.get(GATEIO_SPOT_KLINE_URL, params={"currency_pair": symbol, "interval": interval, "limit": limit}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        klines = []
        for item in data:
            klines.append({"timestamp": int(item[0]) * 1000, "close": float(item[2]), "high": float(item[3]), "low": float(item[4]), "open": float(item[5]), "volume": float(item[1])})
        log.info(f"Gate.io 现货K线获取成功: {symbol}, 共 {len(klines)} 根")
        return klines
    except requests.exceptions.RequestException as e:
        log.error(f"Gate.io 现货API请求失败: {e}")
        return None

def fetch_binance_alpha_data(symbol: str):
    try:
        resp = requests.get(BINANCE_ALPHA_TOKEN_LIST, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for token in data.get("data", []):
            if token.get("symbol", "").upper() == symbol.upper():
                return {"alphaId": token.get("alphaId"), "price": token.get("price"), "percentChange24h": token.get("percentChange24h"), "volume24h": token.get("volume24h")}
    except Exception:
        pass
    return None

def calc_ma(klines, period=10):
    if len(klines) < period:
        return None
    return sum(k["close"] for k in klines[-period:]) / period

def calc_atr(klines, period=14):
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        trs.append(max(klines[i]["high"] - klines[i]["low"], abs(klines[i]["high"] - klines[i-1]["close"]), abs(klines[i]["low"] - klines[i-1]["close"])))
    return sum(trs) / period

def calc_rsi(klines, period=14):
    if len(klines) < period + 1:
        return None
    gains, losses = 0, 0
    for i in range(-period, 0):
        change = klines[i]["close"] - klines[i-1]["close"]
        if change >= 0:
            gains += change
        else:
            losses += abs(change)
    if losses == 0:
        return 100
    return 100 - (100 / (1 + (gains / losses)))

def find_recent_high(klines, lookback=50):
    return max(k["high"] for k in (klines[-lookback:] if len(klines) > lookback else klines))

def find_recent_low(klines, lookback=50):
    return min(k["low"] for k in (klines[-lookback:] if len(klines) > lookback else klines))

def build_market_data(symbol, asset_type):
    market_data = {"symbol": symbol, "asset_type": asset_type, "fetch_status": "ok", "klines": None, "current_price": None, "funding_rate": None, "open_interest": None, "ma10": None, "atr": None, "rsi": None, "recent_high": None, "recent_low": None}

    if asset_type == "futures":
        klines = fetch_hyperliquid_klines(symbol)
        if not klines:
            market_data["fetch_status"] = "fetch_failed"
            return market_data
        market_data.update({"klines": klines, "current_price": klines[-1]["close"], "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14), "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines, 50), "recent_low": find_recent_low(klines, 50)})
        funding_data = fetch_hyperliquid_funding(symbol)
        if funding_data:
            market_data["funding_rate"] = funding_data.get("funding_rate")
            market_data["open_interest"] = funding_data.get("open_interest")

    elif asset_type == "spot":
        klines = fetch_gateio_spot_klines(symbol)
        if not klines:
            market_data["fetch_status"] = "fetch_failed"
            return market_data
        market_data.update({"klines": klines, "current_price": klines[-1]["close"], "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14), "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines, 50), "recent_low": find_recent_low(klines, 50)})

    elif asset_type == "alpha":
        alpha_data = fetch_binance_alpha_data(symbol)
        if not alpha_data:
            market_data["fetch_status"] = "data_unavailable"
            return market_data
        market_data["current_price"] = alpha_data.get("price")

    return market_data

def load_config():
    with open("config/config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    config = load_config()
    watchlist = config.get("watchlist", [])
    active_strategy_name = config.get("active_strategy", "v1_default")
    shadow_strategies = config.get("shadow_strategies", [])
    notify_on_no_signal = config.get("notify_on_no_signal", False)

    strategy_override = os.environ.get("STRATEGY_OVERRIDE", "").strip()
    if strategy_override:
        active_strategy_name = strategy_override

    active_strategy = load_strategy(active_strategy_name)
    shadow_instances = {name: load_strategy(name) for name in shadow_strategies}

    all_results = []
    triggered_any = False

    for item in watchlist:
        symbol, asset_type = item["symbol"], item.get("type", "futures")
        log.info(f"=== 处理 {symbol} ({asset_type}) ===")
        market_data = build_market_data(symbol, asset_type)

        if market_data["fetch_status"] != "ok":
            all_results.append({"symbol": symbol, "asset_type": asset_type, "status": market_data["fetch_status"], "strategy_result": None})
            continue

        try:
            result = active_strategy.evaluate(symbol, asset_type, market_data)
        except Exception as e:
            log.error(f"[{symbol}] 策略评估异常: {e}")
            result = None

        if result is None:
            log.warning(f"[{symbol}] 策略返回为空")
            all_results.append({"symbol": symbol, "asset_type": asset_type, "status": "strategy_error", "strategy_result": None})
            continue

        log.info(f"[{symbol}] 策略结果: 轨道A={result.get('track_a', {}).get('score', 'N/A')}, 轨道B={result.get('track_b', {}).get('score', 'N/A')}")
        all_results.append({"symbol": symbol, "asset_type": asset_type, "status": "ok", "current_price": market_data.get("current_price"), "strategy_result": result, "market_data": market_data})
        if result.get("triggered"):
            triggered_any = True

    shadow_results = []
    for name, strategy in shadow_instances.items():
        for item in watchlist:
            matching = [r for r in all_results if r["symbol"] == item["symbol"]]
            if matching and matching[0]["status"] == "ok":
                try:
                    sr = strategy.evaluate(item["symbol"], item.get("type", "futures"), matching[0]["market_data"])
                    shadow_results.append({"strategy": name, "symbol": item["symbol"], "track_a": sr.get("track_a", {}).get("score", 0), "track_b": sr.get("track_b", {}).get("score", 0)})
                except Exception:
                    pass

    if not triggered_any and not notify_on_no_signal:
        log.info("无信号触发，静默退出")
        write_signal_log(all_results)
        return

    subject, html = build_email_html(all_results, shadow_results, active_strategy_name, watchlist)
    send_html_email(subject, html)
    write_signal_log(all_results)

def write_signal_log(results):
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/signals_{datetime.now(BJT).strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"time": datetime.now(BJT).isoformat(), "symbol": r["symbol"], "asset_type": r.get("asset_type"), "status": r.get("status"), "triggered": r.get("strategy_result", {}).get("triggered", False) if r.get("strategy_result") else False}, ensure_ascii=False) + "\n")

def build_email_html(results, shadow_results, active_strategy_name, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    symbols_str = ", ".join([f"{w['symbol']}({w.get('type', 'futures')})" for w in watchlist])
    html = f"<html><body style='font-family:Arial,sans-serif;max-width:800px;margin:0 auto;'><h2>📊 多类型标的 合约/现货/Alpha 双轨监控报告</h2><p><b>时间：</b>{now} (北京时间)</p><p><b>正式策略：</b>{active_strategy_name}</p><p><b>监控列表：</b>{symbols_str}</p><hr>"
    html += '<h3>🚨 触发信号详情</h3>'
    triggered_found = False
    for r in results:
        if r.get("status") != "ok":
            html += f'<p style="color:#999;">🎯 <b>{r["symbol"]}</b> ({r.get("asset_type")}) — 数据获取失败（{r.get("status")}）</p>'
            continue
        sr = r.get("strategy_result", {})
        if not sr.get("triggered"):
            continue
        triggered_found = True
        html += f'<p><b>🎯 {r["symbol"]}</b>（{r.get("asset_type")} | 当前价：${r.get("current_price", "N/A")}）</p>'
        ta, tb = sr.get("track_a", {}), sr.get("track_b", {})
        html += f'<p>🟥 轨道A：见顶做空（{ta.get("score", 0)}/4）</p><ul>' + "".join(f"<li>{k}: {v}</li>" for k, v in ta.get("details", {}).items()) + '</ul>'
        html += f'<p>🟩 轨道B：暴跌抄底（{tb.get("score", 0)}/3）</p><ul>' + "".join(f"<li>{k}: {v}</li>" for k, v in tb.get("details", {}).items()) + '</ul>'
        md = r.get("market_data", {})
        atr, ma10, rh, rl, cp = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low"), r.get("current_price")
        html += '<p><b>📐 动态仓位管理</b></p><ul>'
        if atr and rh and cp:
            html += f'<li>做空硬止损价：${rh + 1.5 * atr:.4f}（前高${rh:.4f} + 1.5×ATR${atr:.4f}）</li>'
        if atr and rl and cp:
            html += f'<li>做多硬止损价：${rl - 1.5 * atr:.4f}（近期低点${rl:.4f} - 1.5×ATR${atr:.4f}）</li>'
        if cp:
            html += f'<li>移动止盈触发价1：${cp * 1.2:.4f}</li><li>移动止盈触发价2：${cp * 1.5:.4f}</li>'
        if ma10:
            html += f'<li>MA10 动态离场价：${ma10:.4f}</li>'
        html += '</ul>'
    if not triggered_found:
        html += '<p>无标的触发信号</p>'
    html += '<hr><h3>⏳ 未触发信号的标的</h3><ul>'
    for r in results:
        if r.get("status") != "ok":
            html += f'<li>{r["symbol"]}：{r.get("status")}</li>'
        elif not r.get("strategy_result", {}).get("triggered"):
            sr = r.get("strategy_result", {})
            html += f'<li>{r["symbol"]}：等待中（A {sr.get("track_a", {}).get("score", 0)}/3，B {sr.get("track_b", {}).get("score", 0)}/3）</li>'
    html += '</ul>'
    if shadow_results:
        html += '<hr><h3>🔬 影子策略观察</h3><ul>'
        for sr in shadow_results:
            html += f'<li>{sr["strategy"]}：{sr["symbol"]} 轨道A {sr["track_a"]}/3，轨道B {sr["track_b"]}/3</li>'
        html += '</ul>'
    return f"【多标的监控】{now} | 🟢信号触发", html + '</body></html>'

if __name__ == "__main__":
    main()
