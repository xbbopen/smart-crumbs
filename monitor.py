"""
多标的合约/现货/Alpha 双轨监控 - 主引擎
数据源：
- 合约（futures）：Hyperliquid 公开 Info API（无 IP 限制）
- 现货（spot）：Gate.io 公开 API
- Alpha：币安 Alpha API（可能被 451 封锁，自动跳过）
"""

import json
import os
import time
import sys
import logging
from datetime import datetime, timezone, timedelta

import requests

from email_sender import send_html_email
from strategies.loader import load_strategy

# ========== 配置日志 ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ========== 常量 ==========
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
GATEIO_SPOT_KLINE_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
BINANCE_ALPHA_TOKEN_LIST = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"

# 北京时间
BJT = timezone(timedelta(hours=8))


# ========== Hyperliquid 数据获取 ==========

def hyperliquid_post(payload: dict) -> dict:
    """向 Hyperliquid Info API 发送 POST 请求"""
    try:
        resp = requests.post(
            HYPERLIQUID_INFO_URL,
            json=payload,
            timeout=15,
            headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"Hyperliquid API 请求失败: {e}")
        return None


def fetch_hyperliquid_klines(symbol: str, interval: str = "30m", limit: int = 100):
    """
    获取 Hyperliquid 合约 K 线
    Hyperliquid 的 coin 名称是基础币种，如 NEAR、BTC、ETH
    """
    coin = symbol.replace("_USDT", "").replace("-USDT", "").replace("USDT", "")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (limit * 30 * 60 * 1000)

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms
        }
    }

    data = hyperliquid_post(payload)
    if not data or not isinstance(data, list):
        log.warning(f"Hyperliquid K线数据为空或格式错误: {symbol}")
        return None

    klines = []
    for item in data:
        klines.append({
            "timestamp": item["t"],
            "open": float(item["o"]),
            "high": float(item["h"]),
            "low": float(item["l"]),
            "close": float(item["c"]),
            "volume": float(item["v"]),
        })

    log.info(f"Hyperliquid K线获取成功: {symbol}, 共 {len(klines)} 根")
    return klines


def fetch_hyperliquid_funding(symbol: str):
    """获取 Hyperliquid 资金费率"""
    coin = symbol.replace("_USDT", "").replace("-USDT", "").replace("USDT", "")
    payload = {"type": "metaAndAssetCtxs"}
    data = hyperliquid_post(payload)
    if not data:
        return None

    try:
        meta = data[0]
        ctxs = data[1]
        universe = meta.get("universe", [])
        for i, asset in enumerate(universe):
            if asset.get("name", "").upper() == coin.upper():
                ctx = ctxs[i] if i < len(ctxs) else {}
                return {
                    "funding_rate": float(ctx.get("funding", 0)),
                    "open_interest": float(ctx.get("openInterest", 0)),
                    "mark_price": float(ctx.get("markPx", 0)),
                }
    except (IndexError, KeyError, TypeError) as e:
        log.error(f"Hyperliquid 资金费率解析失败: {e}")
    return None


# ========== Gate.io 现货数据获取 ==========

def fetch_gateio_spot_klines(symbol: str, interval: str = "30m", limit: int = 100):
    """获取 Gate.io 现货 K 线（不受美国 IP 限制）"""
    params = {
        "currency_pair": symbol,
        "interval": interval,
        "limit": limit
    }
    try:
        resp = requests.get(GATEIO_SPOT_KLINE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None

        klines = []
        for item in data:
            klines.append({
                "timestamp": int(item[0]) * 1000,
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "open": float(item[5]),
                "volume": float(item[1]),
            })
        log.info(f"Gate.io 现货K线获取成功: {symbol}, 共 {len(klines)} 根")
        return klines
    except requests.exceptions.RequestException as e:
        log.error(f"Gate.io 现货API请求失败: {e}")
        return None


# ========== 币安 Alpha ==========

def fetch_binance_alpha_data(symbol: str):
    """获取币安 Alpha 币数据（可能被 451 封锁）"""
    try:
        resp = requests.get(BINANCE_ALPHA_TOKEN_LIST, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        token_list = data.get("data", [])
        for token in token_list:
            if token.get("symbol", "").upper() == symbol.upper():
                return {
                    "alphaId": token.get("alphaId"),
                    "price": token.get("price"),
                    "percentChange24h": token.get("percentChange24h"),
                    "volume24h": token.get("volume24h"),
                }
    except requests.exceptions.RequestException as e:
        log.warning(f"币安 Alpha API 不可达（可能被 451 封锁）: {e}")
    except (ValueError, KeyError) as e:
        log.warning(f"币安 Alpha 数据解析失败: {e}")
    return None


# ========== 技术指标计算 ==========

def calc_ma(klines, period=10):
    if len(klines) < period:
        return None
    closes = [k["close"] for k in klines[-period:]]
    return sum(closes) / period


def calc_atr(klines, period=14):
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_close = klines[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / period


def calc_rsi(klines, period=14):
    if len(klines) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        change = klines[i]["close"] - klines[i - 1]["close"]
        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def find_recent_high(klines, lookback=50):
    if not klines:
        return None
    subset = klines[-lookback:] if len(klines) > lookback else klines
    return max(k["high"] for k in subset)


def find_recent_low(klines, lookback=50):
    if not klines:
        return None
    subset = klines[-lookback:] if len(klines) > lookback else klines
    return min(k["low"] for k in subset)


# ========== 构建市场数据 ==========

def build_market_data(symbol, asset_type):
    market_data = {
        "symbol": symbol,
        "asset_type": asset_type,
        "fetch_status": "ok",
        "klines": None,
        "current_price": None,
        "funding_rate": None,
        "open_interest": None,
        "long_short_ratio": None,
        "ma10": None,
        "atr": None,
        "rsi": None,
        "recent_high": None,
        "recent_low": None,
    }

    if asset_type == "futures":
        klines = fetch_hyperliquid_klines(symbol)
        time.sleep(1)
        if not klines:
            market_data["fetch_status"] = "fetch_failed"
            return market_data

        market_data["klines"] = klines
        market_data["current_price"] = klines[-1]["close"]
        market_data["ma10"] = calc_ma(klines, 10)
        market_data["atr"] = calc_atr(klines, 14)
        market_data["rsi"] = calc_rsi(klines, 14)
        market_data["recent_high"] = find_recent_high(klines, 50)
        market_data["recent_low"] = find_recent_low(klines, 50)

        funding_data = fetch_hyperliquid_funding(symbol)
        time.sleep(1)
        if funding_data:
            market_data["funding_rate"] = funding_data.get("funding_rate")
            market_data["open_interest"] = funding_data.get("open_interest")

    elif asset_type == "spot":
        klines = fetch_gateio_spot_klines(symbol)
        time.sleep(1)
        if not klines:
            market_data["fetch_status"] = "fetch_failed"
            return market_data

        market_data["klines"] = klines
        market_data["current_price"] = klines[-1]["close"]
        market_data["ma10"] = calc_ma(klines, 10)
        market_data["atr"] = calc_atr(klines, 14)
        market_data["rsi"] = calc_rsi(klines, 14)
        market_data["recent_high"] = find_recent_high(klines, 50)
        market_data["recent_low"] = find_recent_low(klines, 50)

    elif asset_type == "alpha":
        alpha_data = fetch_binance_alpha_data(symbol)
        time.sleep(1)
        if not alpha_data:
            market_data["fetch_status"] = "data_unavailable"
            return market_data
        market_data["current_price"] = alpha_data.get("price")
        market_data["fetch_status"] = "ok"

    return market_data


# ========== 主流程 ==========

def load_config():
    with open("config/config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    log.info("=" * 50)
    log.info("开始运行多标的双轨监控")
    log.info("=" * 50)

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
        symbol = item["symbol"]
        asset_type = item.get("type", "futures")
        log.info(f"\n=== 处理 {symbol} ({asset_type}) ===")

        market_data = build_market_data(symbol, asset_type)

        if market_data["fetch_status"] != "ok":
            log.warning(f"[{symbol}] 数据获取失败: {market_data['fetch_status']}")
            all_results.append({
                "symbol": symbol, "asset_type": asset_type,
                "status": market_data["fetch_status"], "strategy_result": None,
            })
            continue

        result = active_strategy.evaluate(symbol, asset_type, market_data)
        log.info(f"[{symbol}] 策略结果: 状态={market_data['fetch_status']}, "
                 f"轨道A={result.get('track_a', {}).get('score', 'N/A')}, "
                 f"轨道B={result.get('track_b', {}).get('score', 'N/A')}")

        all_results.append({
            "symbol": symbol, "asset_type": asset_type,
            "status": "ok", "current_price": market_data.get("current_price"),
            "strategy_result": result,
            "market_data": {
                "ma10": market_data.get("ma10"), "atr": market_data.get("atr"),
                "rsi": market_data.get("rsi"), "funding_rate": market_data.get("funding_rate"),
                "recent_high": market_data.get("recent_high"), "recent_low": market_data.get("recent_low"),
            }
        })

        if result.get("triggered"):
            triggered_any = True

    shadow_results = []
    for name, strategy in shadow_instances.items():
        for item in watchlist:
            symbol = item["symbol"]
            asset_type = item.get("type", "futures")
            matching = [r for r in all_results if r["symbol"] == symbol]
            if not matching or matching[0]["status"] != "ok":
                continue
            md = matching[0].get("market_data", {})
            try:
                sr = strategy.evaluate(symbol, asset_type, md)
                shadow_results.append({
                    "strategy": name, "symbol": symbol,
                    "track_a": sr.get("track_a", {}).get("score", 0),
                    "track_b": sr.get("track_b", {}).get("score", 0),
                    "triggered": sr.get("triggered", False),
                })
            except Exception as e:
                log.warning(f"影子策略 {name} 评估 {symbol} 失败: {e}")

    if not triggered_any and not notify_on_no_signal:
        log.info("无信号触发，静默退出")
        write_signal_log(all_results)
        return

    subject, html = build_email_html(all_results, shadow_results, active_strategy_name, watchlist)
    send_html_email(subject, html)
    log.info("邮件已发送")
    write_signal_log(all_results)


def write_signal_log(results):
    os.makedirs("logs", exist_ok=True)
    date_str = datetime.now(BJT).strftime("%Y%m%d")
    path = f"logs/signals_{date_str}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for r in results:
            log_entry = {
                "time": datetime.now(BJT).isoformat(),
                "symbol": r["symbol"], "asset_type": r.get("asset_type"),
                "status": r.get("status"),
                "triggered": r.get("strategy_result", {}).get("triggered", False) if r.get("strategy_result") else False,
            }
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def build_email_html(results, shadow_results, active_strategy_name, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    symbols_str = ", ".join([f"{w['symbol']}({w.get('type', 'futures')})" for w in watchlist])

    html = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto;">
    <h2>📊 多类型标的 合约/现货/Alpha 双轨监控报告</h2>
    <p><b>时间：</b>{now} (北京时间)</p>
    <p><b>正式策略：</b>{active_strategy_name} ｜ <b>影子策略：</b>{', '.join(shadow_results[i]['strategy'] for i in range(len(shadow_results))) if shadow_results else '无'}</p>
    <p><b>监控列表：</b>{symbols_str}</p>
    <hr>
    """

    html += '<h3>🚨 触发信号详情</h3>'
    triggered_found = False

    for r in results:
        if r.get("status") != "ok":
            html += f'<p style="color:#999;">🎯 <b>{r["symbol"]}</b> (类型: {r.get("asset_type")}) — 数据获取失败（{r.get("status")}）</p>'
            continue

        sr = r.get("strategy_result", {})
        if not sr.get("triggered"):
            continue

        triggered_found = True
        price = r.get("current_price", "N/A")
        html += f'<p><b>🎯 {r["symbol"]}</b>（类型：{r.get("asset_type")} | 当前价：${price}）</p>'

        ta = sr.get("track_a", {})
        html += f'<p>🟥 轨道A：见顶做空（✅达成{ta.get("score", 0)}个）</p>'
        if ta.get("details"):
            html += "<ul>" + "".join(f"<li>{k}: {v}</li>" for k, v in ta["details"].items()) + "</ul>"

        tb = sr.get("track_b", {})
        html += f'<p>🟩 轨道B：暴跌抄底（✅达成{tb.get("score", 0)}个）</p>'
        if tb.get("details"):
            html += "<ul>" + "".join(f"<li>{k}: {v}</li>" for k, v in tb["details"].items()) + "</ul>"

        md = r.get("market_data", {})
        atr, ma10, rh, rl, cp = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low"), r.get("current_price")

        html += '<p><b>📐 动态仓位管理</b></p><ul>'
        if atr and rh and cp:
            short_stop = rh + 1.5 * atr
            html += f'<li>做空硬止损价：${short_stop:.4f}（前高${rh:.4f} + 1.5×ATR${atr:.4f}）</li>'
            html += f'<li>做空止损空间：{((short_stop - cp) / cp * 100):.2f}%</li>'
        if atr and rl and cp:
            long_stop = rl - 1.5 * atr
            html += f'<li>做多硬止损价：${long_stop:.4f}（近期低点${rl:.4f} - 1.5×ATR${atr:.4f}）</li>'
            html += f'<li>做多止损空间：{((cp - long_stop) / cp * 100):.2f}%</li>'
        if cp:
            html += f'<li>移动止盈触发价1（保护成本）：${cp * 1.2:.4f}（浮盈20%）</li>'
            html += f'<li>移动止盈触发价2（锁定利润）：${cp * 1.5:.4f}（浮盈50%）</li>'
        if ma10:
            html += f'<li>4H MA10 动态离场价：${ma10:.4f}</li>'
        html += '</ul>'

    if not triggered_found:
        html += '<p>无标的触发信号</p>'

    html += '<hr><h3>⏳ 未触发信号的标的</h3><ul>'
    for r in results:
        if r.get("status") != "ok":
            html += f'<li>{r["symbol"]}：{r.get("status")}</li>'
        elif not r.get("strategy_result", {}).get("triggered"):
            sr = r.get("strategy_result", {})
            html += f'<li>{r["symbol"]}：等待中（轨道A {sr.get("track_a", {}).get("score", 0)}/3，轨道B {sr.get("track_b", {}).get("score", 0)}/3）</li>'
    html += '</ul>'

    if shadow_results:
        html += '<hr><h3>🔬 影子策略观察（仅供验证）</h3><ul>'
        for sr in shadow_results:
            html += f'<li>{sr["strategy"]}：{sr["symbol"]} 轨道A {sr["track_a"]}/3，轨道B {sr["track_b"]}/3</li>'
        html += '</ul>'

    html += '</body></html>'
    return f"【多标的监控】{now} | 🟢信号触发", html


if __name__ == "__main__":
    main()
