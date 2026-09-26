import json, os, time, sys, logging
from datetime import datetime, timezone, timedelta
import requests
from email_sender import send_html_email
from strategies.loader import load_strategy

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
GATEIO_SPOT_KLINE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
BJT = timezone(timedelta(hours=8))

# ================= Hyperliquid 合约接口 =================
def hyperliquid_post(payload: dict):
    try:
        resp = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=15, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 500:
            log.warning(f"Hyperliquid 返回 500（可能该币种未上线）")
        else:
            log.warning(f"Hyperliquid API HTTP 错误: {e}")
        return None
    except Exception as e:
        log.warning(f"Hyperliquid API 请求失败: {e}")
        return None

def symbol_to_coin(symbol: str) -> str:
    return symbol.replace("_USDT", "").replace("-USDT", "").replace("USDT", "").upper()

def fetch_hyperliquid_klines(symbol: str, interval: str = "30m", limit: int = 150):
    coin = symbol_to_coin(symbol)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (limit * 30 * 60 * 1000)
    data = hyperliquid_post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms}})
    if not data or not isinstance(data, list):
        log.info(f"[{symbol}] Hyperliquid 未返回数据（coin={coin}），可能未上线")
        return None
    klines = [{"timestamp": item["t"], "open": float(item["o"]), "high": float(item["h"]), "low": float(item["l"]), "close": float(item["c"]), "volume": float(item["v"])} for item in data]
    log.info(f"Hyperliquid K线获取成功: {symbol} (coin={coin}), 共 {len(klines)} 根")
    return klines

def fetch_hyperliquid_funding(symbol: str):
    coin = symbol_to_coin(symbol)
    data = hyperliquid_post({"type": "metaAndAssetCtxs"})
    if not data: return None
    try:
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin:
                return float(ctxs[i].get("funding", 0))
    except Exception:
        pass
    return None

# ================= Gate.io 现货接口 =================
def fetch_gateio_spot_klines(symbol: str, interval: str = "30m", limit: int = 150):
    try:
        resp = requests.get(GATEIO_SPOT_KLINE_URL, params={"currency_pair": symbol, "interval": interval, "limit": limit}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data: return None
        # Gate.io 现货K线格式：[时间戳, 计价货币成交量, 收盘价, 最高价, 最低价, 开盘价, 基础货币成交量]
        klines = [{"timestamp": int(item[0]) * 1000, "volume": float(item[1]), "close": float(item[2]), "high": float(item[3]), "low": float(item[4]), "open": float(item[5])} for item in data]
        log.info(f"Gate.io 现货K线获取成功: {symbol}, 共 {len(klines)} 根")
        return klines
    except Exception as e:
        log.warning(f"Gate.io 现货K线请求失败: {symbol}, {e}")
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
        change = klines[i]["close"] - klines[i-1]["close"]
        if change >= 0: gains += change
        else: losses += abs(change)
    if losses == 0: return 100
    return 100 - (100 / (1 + (gains / losses)))

def find_recent_high(klines, lookback=149):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    return max(k["high"] for k in subset)

def find_recent_low(klines, lookback=149):
    subset = klines[-lookback-1:-1] if len(klines) > lookback+1 else klines[:-1]
    return min(k["low"] for k in subset)

# ================= 数据构建（合约走Hyperliquid，现货走Gate.io） =================
def build_market_data(symbol, asset_type):
    market_data = {"symbol": symbol, "asset_type": asset_type, "fetch_status": "ok", "klines": None, "current_price": None, "funding_rate": None, "ma10": None, "atr": None, "rsi": None, "recent_high": None, "recent_low": None, "data_source": "Unknown"}
    
    if asset_type == "futures":
        klines = fetch_hyperliquid_klines(symbol)
        if not klines:
            market_data["fetch_status"] = "unsupported"
            return market_data
        market_data.update({
            "klines": klines, "current_price": klines[-1]["close"],
            "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
            "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines),
            "recent_low": find_recent_low(klines), "data_source": "Hyperliquid"
        })
        funding = fetch_hyperliquid_funding(symbol)
        if funding is not None:
            market_data["funding_rate"] = funding

    elif asset_type == "spot":
        klines = fetch_gateio_spot_klines(symbol)
        if not klines:
            market_data["fetch_status"] = "fetch_failed"
            return market_data
        market_data.update({
            "klines": klines, "current_price": klines[-1]["close"],
            "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
            "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines),
            "recent_low": find_recent_low(klines), "data_source": "Gate.io 现货"
        })
        # 现货无资金费率，funding_rate 保持为 None

    else:
        market_data["fetch_status"] = "unsupported"
        
    return market_data

# ================= 主流程 =================
def main():
    with open("config/config.json", "r", encoding="utf-8") as f: config = json.load(f)
    watchlist = config.get("watchlist", [])
    active_strategy_name = config.get("active_strategy", "v1_default")
    notify_on_no_signal = config.get("notify_on_no_signal", False)
    active_strategy = load_strategy(active_strategy_name)
    
    all_results, triggered_any = [], False
    for item in watchlist:
        symbol, asset_type = item["symbol"], item.get("type", "futures")
        log.info(f"=== 处理 {symbol} ({asset_type}) ===")
        market_data = build_market_data(symbol, asset_type)
        time.sleep(1.5)  # 防止API限流
        
        if market_data["fetch_status"] != "ok":
            all_results.append({"symbol": symbol, "asset_type": asset_type, "status": market_data["fetch_status"], "strategy_result": None})
            continue
            
        try: result = active_strategy.evaluate(symbol, asset_type, market_data)
        except Exception as e:
            log.error(f"[{symbol}] 策略异常: {e}"); result = None
            
        if result is None:
            all_results.append({"symbol": symbol, "asset_type": asset_type, "status": "strategy_error", "strategy_result": None}); continue
            
        all_results.append({"symbol": symbol, "asset_type": asset_type, "status": "ok", "current_price": market_data.get("current_price"), "strategy_result": result, "market_data": market_data})
        if result.get("triggered"): triggered_any = True

    if not triggered_any and not notify_on_no_signal:
        log.info("无信号触发，静默退出"); return

    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【多标的监控】{now} | 🟢信号触发"
    send_html_email(subject, build_email_html(all_results, active_strategy_name, watchlist))

# ================= 邮件生成 =================
def build_email_html(results, active_strategy_name, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    html = f"<html><body style='font-family:Arial,sans-serif;max-width:800px;margin:0 auto;'><h2>📊 三轨并行监控报告</h2><p><b>时间：</b>{now} (北京时间)</p><p><b>策略：</b>{active_strategy_name} | <b>数据源：</b>合约Hyperliquid / 现货Gate.io</p><hr>"
    html += '<h3>🚨 触发信号详情</h3>'
    triggered_found = False
    
    for r in results:
        if r.get("status") == "unsupported":
            html += f'<p style="color:#999;">⏳ <b>{r["symbol"]}</b> — 暂不支持（无数据源）</p>'
            continue
        if r.get("status") != "ok":
            html += f'<p style="color:#999;">🎯 <b>{r["symbol"]}</b> — 数据失败（{r.get("status")}）</p>'; continue
            
        sr = r.get("strategy_result", {})
        if not sr.get("triggered"): continue
        triggered_found = True
        md = r.get("market_data", {})
        html += f'<p style="font-size:1.1em;"><b>🎯 {r["symbol"]}</b> ({r.get("asset_type")}) | 当前价：${r.get("current_price"):.4f} | 数据源：{md.get("data_source")}</p>'
        
        if sr.get("direction") == "short":
            ta = sr.get("track_2", {})
            html += f'<p>🟥 轨道2（见顶做空）：{ta.get("score", 0)}/4</p><ul>' + "".join(f"<li>{k}: {v}</li>" for k, v in ta.get("details", {}).items()) + '</ul>'
        elif sr.get("direction") == "long_trend":
            ta = sr.get("track_1", {})
            html += f'<p>🟩 轨道1（底部突破）：{ta.get("score", 0)}/4</p><ul>' + "".join(f"<li>{k}: {v}</li>" for k, v in ta.get("details", {}).items()) + '</ul>'
        elif sr.get("direction") == "long_rebound":
            ta = sr.get("track_3", {})
            html += f'<p>🟩 轨道3（暴跌反弹）：{ta.get("score", 0)}/3</p><ul>' + "".join(f"<li>{k}: {v}</li>" for k, v in ta.get("details", {}).items()) + '</ul>'
        
        atr, ma10, rh, rl, cp = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low"), r.get("current_price")
        html += '<div style="background:#f9f9f9;padding:10px;border-left:4px solid #ff9800;">'
        html += '<p><b>📐 《以交易为生》交易计划</b></p><ul>'
        if sr.get("direction") == "short" and atr and rh:
            stop = rh + 1.5 * atr
            html += f'<li>建议做空入场：${cp:.4f}</li><li>硬止损价：${stop:.4f} (前高${rh:.4f} + 1.5×ATR${atr:.4f})</li>'
            html += f'<li>止损空间：{((stop-cp)/cp*100):.2f}% (建议仓位不超过总资金的 {2/((stop-cp)/cp*100)*100:.1f}%)</li>'
        elif sr.get("direction").startswith("long") and atr and rl:
            stop = rl - 1.5 * atr
            html += f'<li>建议做多入场：${cp:.4f}</li><li>硬止损价：${stop:.4f} (近期低点${rl:.4f} - 1.5×ATR${atr:.4f})</li>'
            html += f'<li>止损空间：{((cp-stop)/cp*100):.2f}% (建议仓位不超过总资金的 {2/((cp-stop)/cp*100)*100:.1f}%)</li>'
        if cp:
            html += f'<li>移动止盈1（浮盈20%保护成本）：${cp * 1.2:.4f}</li><li>移动止盈2（浮盈50%锁定利润）：${cp * 1.5:.4f}</li>'
        if ma10: html += f'<li>MA10动态离场线：${ma10:.4f}</li>'
        html += '</ul></div><hr>'
    
    if not triggered_found: html += '<p>无标的触发信号</p>'
    
    html += '<h3>⏳ 未触发标的详情</h3><ul>'
    for r in results:
        if r.get("status") == "unsupported": 
            html += f'<li>{r["symbol"]}：暂不支持</li>'
        elif r.get("status") != "ok": 
            html += f'<li>{r["symbol"]}：{r.get("status")}</li>'
        elif not r.get("strategy_result", {}).get("triggered"):
            sr, md = r.get("strategy_result", {}), r.get("market_data", {})
            t1, t2, t3 = sr.get("track_1", {}), sr.get("track_2", {}), sr.get("track_3", {})
            html += f'<li><b>{r["symbol"]}</b> ({r.get("asset_type")}, 现价${r.get("current_price"):.4f}, 源:{md.get("data_source")})：轨道1 {t1.get("score",0)}/4，轨道2 {t2.get("score",0)}/4，轨道3 {t3.get("score",0)}/3</li>'
    html += '</ul></body></html>'
    return html

if __name__ == "__main__": main()
