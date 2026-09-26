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

def fetch_hyperliquid_metrics(symbol: str):
    """获取资金费率、未平仓合约量（OI）、24h成交量等用于流动性评估"""
    coin = symbol_to_coin(symbol)
    data = hyperliquid_post({"type": "metaAndAssetCtxs"})
    if not data: return None
    try:
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin:
                ctx = ctxs[i] if i < len(ctxs) else {}
                return {
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": float(ctx.get("openInterest", 0)),
                    "day_volume": float(ctx.get("dayNtlVlm", 0))
                }
    except Exception as e:
        log.warning(f"Hyperliquid 指标解析失败: {e}")
    return None

# ================= Gate.io 现货接口 =================
def fetch_gateio_spot_klines(symbol: str, interval: str = "30m", limit: int = 150):
    try:
        resp = requests.get(GATEIO_SPOT_KLINE_URL, params={"currency_pair": symbol, "interval": interval, "limit": limit}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data: return None
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

# ================= 数据构建 =================
def build_market_data(symbol, asset_type):
    market_data = {"symbol": symbol, "asset_type": asset_type, "fetch_status": "ok", "klines": None, "current_price": None, "funding_rate": None, "open_interest": None, "day_volume": None, "ma10": None, "atr": None, "rsi": None, "recent_high": None, "recent_low": None, "data_source": "Unknown"}
    
    if asset_type == "futures":
        klines = fetch_hyperliquid_klines(symbol)
        if not klines:
            market_data["fetch_status"] = "unsupported"
            return market_data
        market_data.update({"klines": klines, "current_price": klines[-1]["close"], "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14), "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines), "recent_low": find_recent_low(klines), "data_source": "Hyperliquid"})
        metrics = fetch_hyperliquid_metrics(symbol)
        if metrics:
            market_data["funding_rate"] = metrics["funding_rate"]
            market_data["open_interest"] = metrics["open_interest"]
            market_data["day_volume"] = metrics["day_volume"]

    elif asset_type == "spot":
        klines = fetch_gateio_spot_klines(symbol)
        if not klines:
            market_data["fetch_status"] = "fetch_failed"
            return market_data
        market_data.update({"klines": klines, "current_price": klines[-1]["close"], "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14), "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines), "recent_low": find_recent_low(klines), "data_source": "Gate.io 现货"})
    else:
        market_data["fetch_status"] = "unsupported"
    return market_data

# ================= 主流程 =================
def main():
    with open("config/config.json", "r", encoding="utf-8") as f: config = json.load(f)
    watchlist = config.get("watchlist", [])
    active_strategy_name = config.get("active_strategy", "v1_default")
    active_strategy = load_strategy(active_strategy_name)
    
    all_results, triggered_any = [], False
    for item in watchlist:
        symbol, asset_type = item["symbol"], item.get("type", "futures")
        log.info(f"=== 处理 {symbol} ({asset_type}) ===")
        market_data = build_market_data(symbol, asset_type)
        time.sleep(1.5)
        
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

    # 强制每半小时无条件发送报告
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【多标的监控】{now} | {'🟢 触发交易信号' if triggered_any else '⏳ 日常巡检报告'}"
    send_html_email(subject, build_email_html(all_results, active_strategy_name, watchlist))

# ================= 邮件展示 =================
def build_email_html(results, active_strategy_name, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    html = f"<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#333;'><h2>📊 三轨并行监控报告</h2>"
    html += f"<p><b>时间：</b>{now} (北京时间) | <b>策略：</b>{active_strategy_name} | <b>数据源：</b>合约Hyperliquid / 现货Gate.io</p><hr>"

    triggered_list = []
    untriggered_list = []
    unsupported_list = []

    for r in results:
        if r.get("status") == "unsupported":
            unsupported_list.append(r)
        elif r.get("status") != "ok":
            untriggered_list.append(r)
        elif r.get("strategy_result", {}).get("triggered"):
            triggered_list.append(r)
        else:
            untriggered_list.append(r)

    # ========== 1. 触发信号部分 ==========
    html += "<h3>🚨 触发信号详情</h3>"
    if not triggered_list:
        html += "<p style='color:#888;'>当前无标的触发交易信号。</p>"
    else:
        for r in triggered_list:
            md = r.get("market_data", {})
            sr = r.get("strategy_result", {})
            direction = sr.get("direction")
            track_key = "track_1" if direction == "long_trend" else ("track_2" if direction == "short" else "track_3")
            track_name = {"track_1": "底部突破做多", "track_2": "见顶做空", "track_3": "暴跌反弹做多"}[track_key]
            ta = sr.get(track_key, {})
            
            html += f"<div style='border:2px solid #e74c3c; padding:15px; margin-bottom:20px; border-radius:8px; background-color:#fff9f9;'>"
            html += f"<h4>🎯 <b>{r['symbol']}</b> ({r.get('asset_type')}) | 当前价：${r.get('current_price'):.4f} | 数据源：{md.get('data_source')}</h4>"
            html += f"<p><b>✅ 触发策略：{track_name} (得分 {ta.get('score', 0)})</b></p>"
            html += f"<p><b>触发原因：</b>{sr.get('reason', '满足条件')}</p>"
            
            html += "<p><b>🔍 信号明细：</b></p><ul>"
            for k, v in ta.get("details", {}).items():
                html += f"<li><b>{k}:</b> {v}</li>"
            html += "</ul>"

            atr, ma10, rh, rl, cp = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low"), r.get("current_price")
            html += "<div style='background:#f0f8ff; padding:10px; border-left:5px solid #2980b9;'>"
            html += "<p><b>📐 《以交易为生》交易计划</b></p><ul>"
            if direction == "short" and atr and rh:
                stop = rh + 1.5 * atr
                risk_pct = (stop - cp) / cp
                position_pct = min(1.0, 0.02 / risk_pct) if risk_pct > 0 else 1.0
                html += f"<li>建议做空入场：${cp:.4f}</li>"
                html += f"<li>硬止损价：${stop:.4f} (前高${rh:.4f} + 1.5×ATR${atr:.4f})</li>"
                html += f"<li>止损空间：{risk_pct*100:.2f}%</li>"
                html += f"<li>✅ 2%资金管理建议：总仓位不超过 <b>{position_pct*100:.1f}%</b></li>"
            elif direction and direction.startswith("long") and atr and rl:
                stop = rl - 1.5 * atr
                risk_pct = (cp - stop) / cp
                position_pct = min(1.0, 0.02 / risk_pct) if risk_pct > 0 else 1.0
                html += f"<li>建议做多入场：${cp:.4f}</li>"
                html += f"<li>硬止损价：${stop:.4f} (近期低点${rl:.4f} - 1.5×ATR${atr:.4f})</li>"
                html += f"<li>止损空间：{risk_pct*100:.2f}%</li>"
                html += f"<li>✅ 2%资金管理建议：总仓位不超过 <b>{position_pct*100:.1f}%</b></li>"
            if cp:
                html += f"<li>移动止盈1（浮盈20%保护成本）：${cp * 1.2:.4f}</li>"
                html += f"<li>移动止盈2（浮盈50%锁定利润）：${cp * 1.5:.4f}</li>"
            if ma10: html += f"<li>MA10动态离场线：${ma10:.4f}</li>"
            html += "</ul></div></div>"

    # ========== 2. 未触发信号部分 ==========
    html += "<hr><h3>⏳ 未触发标的详情（完整信号与杠杆建议）</h3>"
    for r in untriggered_list:
        if r.get("status") != "ok":
            html += f"<p style='color:#999;'>🎯 <b>{r['symbol']}</b> — 数据获取异常 ({r.get('status')})</p>"
            continue
        
        md = r.get("market_data", {})
        sr = r.get("strategy_result", {})
        oi = md.get("open_interest")
        day_vol = md.get("day_volume")
        
        # 根据 OI 评估流动性
        if oi is None:
            lev_advice = "无法获取OI数据，建议谨慎使用杠杆。"
        elif oi > 100_000_000:
            lev_advice = "【高流动性】(OI>1亿美元) 建议杠杆：10x-20x（结合2%止损规则，实际保证金占用需严格计算）。"
        elif oi > 10_000_000:
            lev_advice = "【中等流动性】(OI>1000万美元) 建议杠杆：5x-10x。"
        else:
            lev_advice = "【低流动性】(OI<1000万美元) 建议杠杆：2x-3x（极易被插针爆仓，建议极低杠杆）。"
            
        html += f"<div style='border:1px solid #ddd; padding:10px; margin-bottom:15px; border-radius:5px;'>"
        html += f"<h4>📌 {r['symbol']} ({r.get('asset_type')}) | 现价：${r.get('current_price'):.4f} | 数据源：{md.get('data_source')}</h4>"
        if oi:
            html += f"<p style='color:#e67e22;'><b>💡 流动性建议：</b>{lev_advice}</p>"
        
        for track_key, track_name in [("track_1", "【底部突破做多】"), ("track_2", "【见顶做空】"), ("track_3", "【暴跌反弹做多】")]:
            ta = sr.get(track_key, {})
            score = ta.get("score", 0)
            max_score = 4 if track_key in ["track_1", "track_2"] else 3
            html += f"<p><b>{track_name} 得分：{score}/{max_score}</b></p>"
            html += "<ul style='color:#555; font-size:0.95em; margin-top:2px;'>"
            for k, v in ta.get("details", {}).items():
                html += f"<li>{k}: {v}</li>"
            html += "</ul>"
        html += "</div>"

    # ========== 3. 暂不支持部分 ==========
    if unsupported_list:
        html += "<hr><h3>⚠️ 暂不支持的标的</h3><ul>"
        for r in unsupported_list:
            html += f"<li>{r['symbol']}：Hyperliquid 未上线该币种（合约）或 Gate.io 无数据（现货）</li>"
        html += "</ul>"

    html += "</body></html>"
    return html

if __name__ == "__main__": main()
