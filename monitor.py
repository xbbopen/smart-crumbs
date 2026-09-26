# -*- coding: utf-8 -*-
"""
main 入口：读取配置 -> 拉取数据 -> 加载策略 -> 计算信号 -> 写日志 -> 发邮件。

monitor.py 只负责"调度"，不含任何具体信号判定逻辑（判定在 strategies 内）。
数据请求顺序：优先 Gate.io，失败则尝试 Hyperliquid，仍失败标记"数据获取失败"。

数据源：
  - futures: Gate.io 合约（K线/资金费率/合约统计）
  - spot   : Gate.io 现货（仅K线，跳过资金费率/OI/多空比）
  - alpha  : 币安 Alpha 官方公开列表 + K线(降级到现货K线)，被451则标记跳过
  - 补充   : Hyperliquid 链上数据（参考，不可用则忽略）
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import email_sender
from strategies.base import normalize_candles
from strategies.loader import load_strategy

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "config.json"
LOG_DIR = BASE_DIR / "logs"

CN_TZ = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}

# ---------------------------------------------------------------------------
# 网络请求工具
# ---------------------------------------------------------------------------

def http_get_json(url: str, params=None, timeout: int = 20):
    """GET 请求并解析 JSON；失败抛异常，由调用方决定是否降级。"""
    resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def http_post_json(url: str, payload=None, timeout: int = 20):
    """POST 请求（Hyperliquid 用）。"""
    resp = requests.post(url, json=payload or {}, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Gate.io 数据
# ---------------------------------------------------------------------------

def fetch_gate_futures_candles(symbol: str) -> list:
    url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
    data = http_get_json(url, {"contract": symbol, "interval": "30m", "limit": 100})
    return normalize_candles(data, source="gate")


def fetch_gate_spot_candles(symbol: str) -> list:
    url = "https://api.gateio.ws/api/v4/spot/candlesticks"
    data = http_get_json(url, {"currency_pair": symbol, "interval": "30m", "limit": 100})
    return normalize_candles(data, source="gate")


def fetch_gate_funding(symbol: str):
    """返回 (latest_rate:float, recent_all_positive:bool)。失败抛异常。"""
    url = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"
    data = http_get_json(url, {"contract": symbol})
    if not data:
        return None, False
    vals = [float(x.get("r", 0)) for x in data]
    latest = vals[0] if vals else None
    recent_positive = all(x > 0 for x in vals[:3]) if vals else False
    return latest, recent_positive


def fetch_gate_contract_stats(symbol: str):
    """返回 {oi, oi_peak, oi_low, lsr, lsr_taker}。失败抛异常。"""
    url = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"
    data = http_get_json(url, {"contract": symbol})
    if not data:
        return {}
    ois = [float(x.get("open_interest", 0)) or 0 for x in data]
    ois = [o for o in ois if o > 0]
    last = data[-1]
    return {
        "oi": float(last.get("open_interest", 0)) or (max(ois) if ois else None),
        "oi_peak": max(ois) if ois else None,
        "oi_low": min(ois) if ois else None,
        "lsr": float(last.get("lsr_account")) if last.get("lsr_account") is not None else None,
        "lsr_taker": float(last.get("lsr_taker")) if last.get("lsr_taker") is not None else None,
    }


# ---------------------------------------------------------------------------
# Binance Alpha 数据
# ---------------------------------------------------------------------------

ALPHA_LIST_URL = ("https://www.binance.com/bapi/defi/v1/public/"
                  "wallet-direct/buw/wallet/cex/alpha/all/token/list")


def fetch_alpha_token_list() -> dict:
    """返回 {symbol_upper: alphaId}。被 451/网络错误抛异常。"""
    data = http_get_json(ALPHA_LIST_URL, timeout=25)
    token_list = (data or {}).get("data") or []
    mapping = {}
    for tok in token_list:
        sym = (tok.get("symbol") or "").upper()
        aid = tok.get("alphaId") or tok.get("id") or tok.get("alpha_id")
        if sym and aid is not None:
            mapping[sym] = str(aid)
    return mapping


def fetch_alpha_kline(symbol: str, alpha_id: str, gate_symbol: str) -> list:
    """
    尝试多个端点获取 Alpha 价格K线，全部失败抛异常。
    1) Binance Alpha bapi K线（公开，可能451）
    2) Binance 现货 K线
    3) Gate.io 现货 K线（首选降级，避开币安 451）
    """
    attempts = [
        ("alpha_bapi", None,
         {"alphaId": alpha_id, "type": "1m", "interval": "30m", "limit": "100"}),
        ("binance_spot", "https://api.binance.com/api/v3/klines",
         {"symbol": gate_symbol, "interval": "30m", "limit": 100}),
        ("gate_spot", "https://api.gateio.ws/api/v4/spot/candlesticks",
         {"currency_pair": gate_symbol, "interval": "30m", "limit": 100}),
    ]
    for name, url, params in attempts:
        try:
            if name == "alpha_bapi":
                # 公开端点路径可能随官方调整，失败即降级
                u = ("https://www.binance.com/bapi/defi/v1/public/"
                     "wallet-direct/buw/v1/public/alpha/candle")
                data = http_get_json(u, params, timeout=25)
                arr = (data or {}).get("data") or []
                return normalize_candles(arr, source="binance")
            raw = http_get_json(url, params, timeout=20) if url else []
            return normalize_candles(raw, source="binance" if name == "binance_spot" else "gate")
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("所有 Alpha K线端点均不可达")


# ---------------------------------------------------------------------------
# Hyperliquid 数据（参考信号，优先标注不可用则忽略）
# ---------------------------------------------------------------------------

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def fetch_hyperliquid(symbol: str) -> dict:
    """拉取 Hyperliquid 链上 OI/标记价/资金费率，作参考信号。失败返回 {}。"""
    base = symbol.split("_")[0].upper()
    try:
        data = http_post_json(HL_INFO_URL, {"type": "metaAndAssetCtxs"}, timeout=15)
        universe = (data or {}).get("universe") or []
        ctxs = (data or {}).get("assetCtxs") or []
        for idx, asset in enumerate(universe):
            if (asset.get("name") or "").upper() == base and idx < len(ctxs):
                ctx = ctxs[idx]
                funding = None
                try:
                    funding = float(ctx.get("funding"))
                except (TypeError, ValueError):
                    passes = True
                mark = None
                try:
                    mark = float(ctx.get("markPx"))
                except (TypeError, ValueError):
                    mark = None
                oi = None
                try:
                    oi = float(ctx.get("openInterest"))
                except (TypeError, ValueError):
                    oi = None
                # 参考信号启发式：资金费率深度为负，或价格在近端高位回落，视为偏空线索
                bearish = False
                if funding is not None and funding < -0.0005:
                    bearish = True
                return {
                    "markPx": mark,
                    "openInterest": oi,
                    "funding": funding,
                    "bearish": bearish,
                    "hint": (f"标记价{mark}" if mark is not None else "标记价不可用")
                            + ("，链上资金费率为负(偏空)" if bearish else ""),
                }
        return {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# 组装某标的的 market_data（含降级链路）
# ---------------------------------------------------------------------------

def build_market_data(item: dict) -> dict:
    """返回 (md, status_note)。status_note 用于标记不可用/失败原因，不编造数据。"""
    symbol = item["symbol"]
    atype = item["type"]
    md = {"candles": [], "funding_rate": None, "funding_positive": False,
          "oi": None, "oi_peak": None, "oi_low": None, "lsr": None,
          "trade_long_ratio": None, "hyperliquid": None}
    status_note = ""

    try:
        if atype == "futures":
            md["candles"] = fetch_gate_futures_candles(symbol)
            time.sleep(1)  # 防限流
            fr, fr_pos = fetch_gate_funding(symbol)
            md["funding_rate"] = fr
            md["funding_positive"] = fr_pos
            time.sleep(1)
            try:
                stats = fetch_gate_contract_stats(symbol)
                md.update(stats)
                md["trade_long_ratio"] = stats.get("lsr_taker")
            except Exception as e:  # noqa: BLE001
                print(f"[{symbol}] contract_stats 失败: {e}")
            # 补充 Hyperliquid（参考，失败忽略）
            time.sleep(1)
            hl = fetch_hyperliquid(symbol)
            if hl:
                md["hyperliquid"] = hl

        elif atype == "spot":
            md["candles"] = fetch_gate_spot_candles(symbol)
            # 现货：跳过资金费率/OI/多空比，仅技术面

        elif atype == "alpha":
            token_map = fetch_alpha_token_list()
            sym = symbol.upper()
            if sym not in token_map:
                status_note = (f"该 Alpha 币（{symbol}）不在币安 Alpha 列表中，数据不可用")
                return md, status_note
            alpha_id = token_map[sym]
            # 现货降级用 gate_symbol：alpha 代币一般是裸符号(如 UGAS)，拼 _USDT
            gate_symbol = symbol.upper()
            if "_USDT" not in gate_symbol:
                gate_symbol = gate_symbol + "_USDT"
            try:
                md["candles"] = fetch_alpha_kline(symbol, alpha_id, gate_symbol)
            except Exception as e:  # noqa: BLE001
                if "451" in str(e) or isinstance(e, requests.exceptions.HTTPError):
                    status_note = "币安 Alpha API 不可达（451），本次跳过"
                else:
                    status_note = "Alpha 数据不可用"
                return md, status_note
        else:
            status_note = f"未知标的类型 {atype}"
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        status_note = f"数据获取失败（HTTP {code}）" if code != 451 else "数据获取失败（451，地理限制）"
        return md, status_note
    except Exception as e:  # noqa: BLE001
        status_note = f"数据获取失败: {str(e)[:120]}"
        return md, status_note

    if not md.get("candles"):
        status_note = status_note or "数据获取失败（无K线返回）"
    return md, status_note


# ---------------------------------------------------------------------------
# 结果判断与日志
# ---------------------------------------------------------------------------

def has_alert(result: dict) -> bool:
    """是否有任何预警或触发信号。"""
    for track in ("track_a", "track_b"):
        t = result.get(track)
        if t and isinstance(t, dict):
            if t.get("warning") or t.get("triggered"):
                return True
    return False


def write_log(run: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(CN_TZ).strftime("%Y%m%d")
    path = LOG_DIR / f"signals_{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")
    print(f"[log] 已写入 {path}")


# ---------------------------------------------------------------------------
# 邮件 HTML 组装
# ---------------------------------------------------------------------------

def _fmt_price(v):
    if v is None:
        return "N/A"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1000:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def _fmt_pct(v):
    if v is None:
        return "N/A"
    return f"{v:.2f}%"


def _track_block(title, icon, track) -> str:
    if not track or not isinstance(track, dict):
        return ""
    count = track.get("count", 0)
    total = track.get("total", 3)
    action = track.get("action", "观察")
    rows = []
    for s in track.get("core") or []:
        mark = "✅" if s.get("met") else "⬜"
        rows.append(f"<tr><td>{mark}</td><td>{s.get('name','')}</td>"
                    f"<td>{s.get('note','')}</td></tr>")
    # 杠杆辅助项（futures）
    leva = track.get("leva") or []
    for s in leva:
        mark = "🟨" if s.get("met") else "⬜"
        rows.append(f"<tr><td>{mark}</td><td><em>{s.get('name','')}</em></td>"
                    f"<td>{s.get('note','')}</td></tr>")
    content = "".join(rows)
    # 仓位（触发时才含）
    pos_html = ""
    pos = track.get("position")
    if pos and pos.get("stops_calculated"):
        pos_html = (
            f"<tr><td colspan=3><b>建议入场价：</b>{_fmt_price(pos.get('entry_reference'))}　"
            f"<b>硬止损价：</b>{_fmt_price(pos.get('hard_stop'))}（{pos.get('stop_calc')}）</td></tr>"
            f"<tr><td colspan=3><b>止损空间：</b>{_fmt_pct(pos.get('stop_distance_pct'))}（用于2%规则）</td></tr>"
            f"<tr><td colspan=3><b>移动止盈触发价①（浮盈20%移损至成本）：</b>{_fmt_price(pos.get('tp1_breakeven'))}</td></tr>"
            f"<tr><td colspan=3><b>移动止盈触发价②（浮盈50%移损至成本+30%）：</b>{_fmt_price(pos.get('tp2_cost_plus30'))}</td></tr>"
            f"<tr><td colspan=3><b>4H MA10 动态离场价：</b>{_fmt_price(pos.get('ma10_exit'))}（跌破清仓）</td></tr>"
        )
    elif pos:
        pos_html = f"<tr><td colspan=3 style='color:#b00020'>{pos.get('note','该指标暂不可用，止损止盈价格无法计算')}</td></tr>"

    status = "🚨 已触发" if track.get("triggered") else ("⚠️ 预警" if track.get("warning") else "观察中")
    color = "#b00020" if track.get("triggered") else ("#d97706" if track.get("warning") else "#666")
    return (f"<h3 style='color:{color}'>{icon} {title}（{action} ✅{count}/{total}）{status}</h3>"
            f"<table border=0 cellpadding=5 cellspacing=0 style='border-collapse:collapse;width:100%'>"
            f"{content}{pos_html}</table>")


def build_html(config, results, shadow_results, now_str) -> str:
    act = config["active_strategy"]
    shadows = ", ".join(config.get("shadow_strategies") or ["无"])
    watch = ", ".join(f"{r.get('symbol')}({r.get('type')})" for r in results)
    rows = []
    for r in results:
        sym = r["symbol"]
        typ = r["type"]
        price = _fmt_price(r["price"])
        header = f"<h2>🎯 {sym}（类型：{typ} | 当前价：{price}）</h2>"
        if r.get("status") != "ok":
            rows.append(f"{header}<p style='color:#888'>{r.get('status_note','数据不可用')}</p>")
            continue
        body = ""
        if r.get("track_a"):
            body += _track_block("轨道A", "🟥", r["track_a"])
        if r.get("track_b"):
            body += _track_block("轨道B", "🟩", r["track_b"])
        if not body:
            body = "<p>等待中，暂无信号</p>"
        rows.append(header + body)

    # 未触发 / 不可用标的静默观察区
    quiet = []
    for r in results:
        if r.get("status") != "ok":
            quiet.append(f"{r.get('symbol')}：{r.get('status_note','数据不可用')}")
        elif not has_alert(r):
            a = r.get("track_a") or {}
            b = r.get("track_b") or {}
            quiet.append(f"{r.get('symbol')}：轨道A {a.get('count',0)}/4，轨道B {b.get('count',0)}/3，等待中")
    quiet_html = ""
    if quiet:
        quiet_html = ("<h3>⏳ 未触发信号的标的（静默观察）</h3><ul>"
                      + "".join(f"<li>{q}</li>" for q in quiet) + "</ul>")

    # 影子策略观察
    shadow_html = ""
    if shadow_results:
        items = []
        for s in shadow_results:
            a = s.get("track_a") or {}
            b = s.get("track_b") or {}
            items.append(f"<li>{s.get('symbol')} 轨道A {a.get('count',0)}/{a.get('total') or 4 if a else 0}，"
                         f"轨道B {b.get('count',0)}/3，未触发</li>")
        shadow_html = ("<h3>🔬 影子策略观察（仅供验证，不影响正式信号）</h3>"
                       + "<ul>" + "".join(items) + "</ul>")

    return f"""
<html><head><meta charset="utf-8"><style>
body{{font-family:system-ui,Arial,sans-serif;background:#f5f6f8;margin:0;padding:20px}}
.wrap{{max-width:860px;margin:0 auto;background:#fff;border-radius:10px;padding:24px;box-shadow:0 2px 10px rgba(0,0,0,.08)}}
h1{{font-size:20px}} h2{{font-size:16px;border-bottom:2px solid #eee;padding-bottom:6px}}
h3{{font-size:14px}} table{{font-size:13px}} td{{border-bottom:1px solid #f0f0f0;vertical-align:top}}
.meta{{color:#666;font-size:13px;margin-bottom:18px}}
</style></head><body><div class="wrap">
<h1>📊 多类型标的 合约/现货/Alpha 双轨监控报告</h1>
<div class="meta">
时间：{now_str}（北京时间）<br>
正式策略：{act} ｜ 影子策略：{shadows}（观察中）<br>
监控列表：{watch}
</div>
<h2 style="color:#b00020">🚨 触发信号详情</h2>
{''.join(rows)}
{quiet_html}
{shadow_html}
<p style="color:#999;font-size:11px;margin-top:20px">本报告由自动化脚本生成，仅为数据监控参考，不构成投资建议。</p>
</div></body></html>"""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    if not CONFIG_PATH.exists():
        print(f"[fatal] 找不到配置文件: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 支持 workflow_dispatch 的 STRATEGY_OVERRIDE
    override = os.environ.get("STRATEGY_OVERRIDE", "").strip()
    if override:
        config["active_strategy"] = override
        print(f"[cfg] 使用 STRATEGY_OVERRIDE: {override}")

    active = config["active_strategy"]
    shadows = [s for s in config.get("shadow_strategies", []) if s != active]
    notify_no_signal = bool(config.get("notify_on_no_signal", False))
    watchlist = config.get("watchlist", [])

    now = datetime.now(CN_TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M")

    results = []
    shadow_results = []

    for item in watchlist:
        symbol = item.get("symbol")
        atype = item.get("type")
        strat_name = item.get("strategy") or active
        print(f"\n=== 处理 {symbol} ({atype}) ===")
        try:
            md, note = build_market_data(item)
        except Exception as e:  # noqa: BLE001
            print(f"[{symbol}] 数据构建失败: {e}")
            results.append({"symbol": symbol, "type": atype, "price": None,
                            "status": "fetch_failed", "status_note": str(e)[:120],
                            "track_a": None, "track_b": None})
            continue

        result = {"symbol": symbol, "type": atype, "price": None,
                  "status": "fetch_failed" if not md["candles"] else "ok",
                  "status_note": note, "track_a": None, "track_b": None}

        if md["candles"]:
            try:
                strategy = load_strategy(strat_name)
                result = strategy.evaluate(symbol, atype, md)
                if result.get("price") is None:
                    result["price"] = md["candles"][-1]["c"] if md["candles"] else None
                result["status_note"] = note or "ok"
            except Exception as e:  # noqa: BLE001
                result["status"] = "fetch_failed"
                result["status_note"] = f"策略执行失败: {str(e)[:120]}"
        results.append(result)
        print(f"  结果: 状态={result.get('status')} "
              f"轨道A={result.get('track_a',{}).get('count') if result.get('track_a') else '-'} "
              f"轨道B={result.get('track_b',{}).get('count') if result.get('track_b') else '-'}")

        # 影子策略：只计算，附在邮件末尾，不触发正式警报
        for sname in shadows:
            try:
                s_strategy = load_strategy(sname)
                s_res = s_strategy.evaluate(symbol, atype, md)
                s_res["symbol"] = symbol
                s_res["type"] = atype
                s_res["_strategy"] = sname
                shadow_results.append(s_res)
            except Exception as e:  # noqa: BLE001
                print(f"[{symbol}] 影子策略 {sname} 失败: {e}")

        time.sleep(1)  # 每个标的之间防限流

    # 日志（无论是否发邮件都记录）
    run = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
           "active_strategy": active,
           "shadow_strategies": shadows,
           "results": results,
           "shadow_results": shadow_results}
    write_log(run)

    # 是否发邮件
    any_alert = any(has_alert(r) for r in results)
    if any_alert or notify_no_signal:
        subject = (f"【合约双轨监控】{now_str} | "
                   + (f"🟢 {'、'.join(r['symbol'] for r in results if has_alert(r))} 触发信号"
                      if any_alert else "🟡 例行观察"))
        html = build_html(config, results, shadow_results, now_str)
        email_sender.send_html_email(subject, html)
        print(f"\n[summary] 发送邮件，主题: {subject}")
    else:
        print("\n[summary] 无标的触发预警/信号，notify_on_no_signal=false，静默退出（不发邮件）")
        for r in results:
            if r.get("status") != "ok":
                silence = r
                break
        # (silence 仅为可读性保留，无需额外动作)


if __name__ == "__main__":
    main()