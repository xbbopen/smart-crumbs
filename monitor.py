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

# ================= 全局格式化工具 =================
def fmt_price(price):
    if price is None: return "N/A"
    p = float(price)
    if p < 1: return f"${p:.4f}"
    elif p < 100: return f"${p:.2f}"
    else: return f"${p:.0f}"

def fmt_num(num):
    if num is None: return "N/A"
    return f"{num:,.2f}"

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
        return None
    klines = [{"timestamp": item["t"], "open": float(item["o"]), "high": float(item["h"]), "low": float(item["l"]), "close": float(item["c"]), "volume": float(item["v"])} for item in data]
    log.info(f"Hyperliquid K线获取成功: {symbol} (coin={coin}), 共 {len(klines)} 根")
    return klines

def fetch_hyperliquid_metrics(symbol: str, current_price: float):
    coin = symbol_to_coin(symbol)
    data = hyperliquid_post({"type": "metaAndAssetCtxs"})
    if not data: return None
    try:
        meta, ctxs = data[0], data[1]
        for i, asset in enumerate(meta.get("universe", [])):
            if asset.get("name", "").upper() == coin:
                ctx = ctxs[i] if i < len(ctxs) else {}
                oi_coins = float(ctx.get("openInterest", 0))
                oi_usd = oi_coins * current_price
                return {
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": oi_usd,
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

# ================= 数据构建（核心降级逻辑） =================
def build_market_data(symbol, asset_type):
    market_data = {"symbol": symbol, "asset_type": asset_type, "fetch_status": "ok", "klines": None, "current_price": None, "funding_rate": None, "open_interest": None, "day_volume": None, "ma10": None, "atr": None, "rsi": None, "recent_high": None, "recent_low": None, "data_source": "Unknown"}
    
    if asset_type == "futures":
        # 1. 首选 Hyperliquid 合约
        klines = fetch_hyperliquid_klines(symbol)
        if klines:
            current_price = klines[-1]["close"]
            market_data.update({
                "klines": klines, "current_price": current_price,
                "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
                "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines),
                "recent_low": find_recent_low(klines), "data_source": "Hyperliquid 合约"
            })
            metrics = fetch_hyperliquid_metrics(symbol, current_price)
            if metrics:
                market_data["funding_rate"] = metrics["funding_rate"]
                market_data["open_interest"] = metrics["open_interest"]
                market_data["day_volume"] = metrics["day_volume"]
            return market_data
        
        # 2. 降级到 Gate.io 现货
        log.warning(f"[{symbol}] Hyperliquid 获取失败，尝试降级到 Gate.io 现货")
        klines = fetch_gateio_spot_klines(symbol)
        if klines:
            market_data.update({
                "klines": klines, "current_price": klines[-1]["close"],
                "ma10": calc_ma(klines, 10), "atr": calc_atr(klines, 14),
                "rsi": calc_rsi(klines, 14), "recent_high": find_recent_high(klines),
                "recent_low": find_recent_low(klines), "data_source": "Gate.io 现货 (降级)"
            })
            # 现货没有资金费率，保持为 None
            return market_data
            
        # 3. 两者都失败
        log.warning(f"[{symbol}] Hyperliquid 和 Gate.io 均无数据，标记为不支持")
        market_data["fetch_status"] = "unsupported"
        return market_data

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

    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【参谋长预警】{now} | {'🔥 发现暴力做单机会！' if triggered_any else '🛡️ 日常巡检报告'}"
    send_html_email(subject, build_email_html(all_results, active_strategy_name, watchlist))

# ================= 邮件展示 =================
def build_email_html(results, active_strategy_name, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    
    ad_banner = """
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; text-align: center; margin-bottom: 20px;">
        <h1 style="margin: 0; font-size: 28px; letter-spacing: 2px;">🐮 牛来参谋长</h1>
        <p style="font-size: 16px; margin: 10px 0 0 0; opacity: 0.9;">怕踏空？怕被割？牛来参谋长，专抓暴涨暴跌暴力反弹！</p>
        <p style="font-size: 18px; font-weight: bold; margin: 5px 0 0 0;">参谋长预警系统，没有感情的赚钱机器。关注我，一起赚！</p>
    </div>
    """
    
    html = f"<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#333;'>"
    html += ad_banner
    html += f"<h2 style='border-bottom: 3px solid #e74c3c; padding-bottom: 10px;'>📊 参谋长三轨并行监控报告</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} (北京时间) | <b>策略：</b>{active_strategy_name} | <b>数据源：</b>合约Hyperliquid / 现货Gate.io</p><hr>"

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
    html += "<h3 style='color: #e74c3c; font-size: 24px;'>🚨 触发交易信号 (重点关注)</h3>"
    if not triggered_list:
        html += "<p style='color:#888; font-size: 16px;'>当前无标的触发交易信号，请保持耐心，等待最佳击球点。</p>"
    else:
        for r in triggered_list:
            md = r.get("market_data", {})
            sr = r.get("strategy_result", {})
            direction = sr.get("direction")
            track_key = "track_1" if direction == "long_trend" else ("track_2" if direction == "short" else "track_3")
            track_name = {"track_1": "底部突破做多", "track_2": "见顶做空", "track_3": "暴跌反弹做多"}[track_key]
            ta = sr.get(track_key, {})
            cp = r.get("current_price")
            atr, ma10, rh, rl = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low")
            
            # 杠杆与流动性建议（基于OI和成交量）
            oi = md.get("open_interest")
            day_vol = md.get("day_volume")
            if oi is None: lev_advice = "OI数据缺失，建议 3x 以下轻仓。"
            elif oi > 500_000_000: lev_advice = "【极高流动性】建议杠杆：20x-50x（请严格计算保证金）。"
            elif oi > 50_000_000: lev_advice = "【高流动性】建议杠杆：10x-20x。"
            elif oi > 5_000_000: lev_advice = "【中等流动性】建议杠杆：5x-10x。"
            else: lev_advice = "【低流动性】建议杠杆：2x-3x（极易插针，轻仓保命）。"
            
            # 2%资金管理计算
            risk_pct = 0
            entry_str = fmt_price(cp)
            stop_str = "N/A"
            if direction == "short" and atr and rh:
                stop = rh + 1.5 * atr
                risk_pct = (stop - cp) / cp
                stop_str = fmt_price(stop)
            elif direction.startswith("long") and atr and rl:
                stop = rl - 1.5 * atr
                risk_pct = (cp - stop) / cp
                stop_str = fmt_price(stop)
                
            position_pct = min(1.0, 0.02 / risk_pct) if risk_pct > 0 else 1.0
            capital = 10000
            max_loss = capital * 0.02
            position_value = max_loss / risk_pct if risk_pct > 0 else 0
            margin_10x = position_value / 10
            coin_amount = position_value / cp if cp else 0

            html += f"<div style='border:3px solid #e74c3c; padding:15px; margin-bottom:20px; border-radius:8px; background-color:#fff9f9; box-shadow: 0 4px 8px rgba(0,0,0,0.1);'>"
            html += f"<h4 style='font-size: 22px; color: #c0392b; margin-top: 0;'>🎯 {r['symbol']} ({r.get('asset_type')}) | 当前价：{entry_str} | 数据源：{md.get('data_source')}</h4>"
            html += f"<p style='font-size: 18px;'><b>✅ 触发策略：{track_name} (得分 {ta.get('score', 0)})</b></p>"
            html += f"<p style='font-size: 16px;'><b>触发原因：</b>{sr.get('reason', '满足条件')}</p>"
            
            html += "<p style='font-size: 16px;'><b>🔍 信号明细：</b></p><ul style='font-size: 15px;'>"
            for k, v in ta.get("details", {}).items():
                html += f"<li><b>{k}:</b> {v}</li>"
            html += "</ul>"

            html += "<div style='background:#f0f8ff; padding:15px; border-left:5px solid #2980b9; margin-top:15px;'>"
            html += "<h4 style='margin-top:0;'>📐 《以交易为生》交易计划与开仓指南</h4><ul style='font-size: 15px;'>"
            html += f"<li>建议入场：{entry_str}</li>"
            html += f"<li>硬止损价：{stop_str} (止损空间 {risk_pct*100:.2f}%)</li>"
            html += f"<li><b>💡 流动性建议：</b>{lev_advice}</li>"
            html += f"<li><b>🛡️ 2%资金管理建议：</b>总仓位价值不超过本金的 <b>{position_pct*100:.1f}%</b></li>"
            html += f"<li><b>📝 具体开仓操作 (以10000U本金为例)：</b><br>"
            html += f"以 10倍杠杆 为例，建议投入保证金 <b>{margin_10x:.2f} USDT</b>，"
            html += f"开仓数量为 <b>{coin_amount:.4f} 个 {r['symbol'].replace('_USDT','')}</b>。<br>"
            html += f"<span style='color:#e67e22;'>（操作提示：在交易所输入该数量，选择10倍杠杆，即可实现2%止损风险控制）</span></li>"
            if cp:
                html += f"<li>移动止盈1（浮盈20%保护成本）：{fmt_price(cp * 1.2)}</li>"
                html += f"<li>移动止盈2（浮盈50%锁定利润）：{fmt_price(cp * 1.5)}</li>"
            if ma10: html += f"<li>MA10动态离场线：{fmt_price(ma10)}</li>"
            html += "</ul></div></div>"

    # ========== 2. 未触发信号部分 ==========
    html += "<hr><h3 style='color: #27ae60; font-size: 20px;'>⏳ 未触发标的详情 (静待时机)</h3>"
    for r in untriggered_list:
        if r.get("status") != "ok":
            html += f"<p style='color:#999;'>🎯 <b>{r['symbol']}</b> — 数据获取异常 ({r.get('status')})</p>"
            continue
        
        md = r.get("market_data", {})
        sr = r.get("strategy_result", {})
        oi = md.get("open_interest")
        day_vol = md.get("day_volume")
        cp = r.get("current_price")
        
        if oi is None:
            lev_advice = "无法获取OI数据，建议谨慎使用杠杆。"
        elif oi > 500_000_000:
            lev_advice = "【极高流动性】建议杠杆：20x-50x。"
        elif oi > 50_000_000:
            lev_advice = "【高流动性】建议杠杆：10x-20x。"
        elif oi > 5_000_000:
            lev_advice = "【中等流动性】建议杠杆：5x-10x。"
        else:
            lev_advice = "【低流动性】建议杠杆：2x-3x（极易插针，轻仓保命）。"
            
        html += f"<div style='border:1px solid #ddd; padding:10px; margin-bottom:15px; border-radius:5px; background-color: #fafafa;'>"
        html += f"<h4 style='margin-top:0;'>📌 {r['symbol']} ({r.get('asset_type')}) | 现价：{fmt_price(cp)} | 数据源：{md.get('data_source')}</h4>"
        if oi:
            html += f"<p style='color:#e67e22; font-weight:bold;'>💡 流动性建议：{lev_advice} (24h成交额: {fmt_num(day_vol)})</p>"
        
        for track_key, track_name in [("track_1", "【底部突破做多】"), ("track_2", "【见顶做空】"), ("track_3", "【暴跌反弹做多】")]:
            ta = sr.get(track_key, {})
            score = ta.get("score", 0)
            max_score = 4 if track_key in ["track_1", "track_2"] else 3
            html += f"<p style='margin-bottom:5px;'><b>{track_name} 得分：{score}/{max_score}</b></p>"
            html += "<ul style='color:#555; font-size:0.9em; margin-top:2px; margin-bottom:10px;'>"
            for k, v in ta.get("details", {}).items():
                html += f"<li>{k}: {v}</li>"
            html += "</ul>"
        html += "</div>"

    # ========== 3. 暂不支持部分 ==========
    if unsupported_list:
        html += "<hr><h3 style='color: #7f8c8d;'>⚠️ 暂不支持的标的</h3><ul>"
        for r in unsupported_list:
            html += f"<li>{r['symbol']}：Hyperliquid 未上线该合约，且 Gate.io 也无对应现货数据</li>"
        html += "</ul>"

    html += "<hr><p style='text-align:center; color:#aaa; font-size:12px;'>本报告由牛来参谋长AI预警系统自动生成 | 数据仅供参考，不构成投资建议 | 合约交易风险极高，请严格设置止损</p>"
    html += "</body></html>"
    return html

if __name__ == "__main__": main()
