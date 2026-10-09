# -*- coding: utf-8 -*-
"""
回测引擎 v10 - 分层回测 + 现实模拟 + 止损空间诊断 + 挂单成交判断

v9 核心改动（保留）：
1. 从 config.json 的 backtest_tiers 读分层标的，不再拉全市场
2. 每笔交易扣滑点（按币种分层：核心 0.02% / 卫星 0.05% / 观察 0.10%）
3. 做空持仓模拟资金费率收入，做多模拟资金费率成本（0.01% / 8h）
4. 报告分三层展示：核心池 / 卫星池 / 观察池
5. 新增"实盘预估收益"列（扣滑点+费率）
6. 无冷却期（按用户要求）
7. 仓位改为固定本金，消除复利偏差
8. manage_position 改用 K 线 high/low 判断止损/移动止盈，更贴近真实盘面
9. 每个标的输出离场原因分布日志 + 止损空间分布日志
10. 报告卡片新增止损空间分布块
11. 强制行缓冲，GitHub Actions 实时输出

🚀 v10 新增：
12. 加 VWAP 计算（与监控对齐）
13. 挂单成交判断：只有当根 K 线触及挂单价才成交（做多用 low，做空用 high）
14. 部分成交按实际权重计算仓位（不补齐到计划仓位）
15. 一档都没成交 → 信号作废，不进 trades
16. 交易卡片显示止损来源（结构位/资金位/1.5%保底/兜底2%）
17. 报告新增"挂单成交统计"和"信号作废数"
18. 报告口径说明增加"挂单成交"、"部分成交"、"双锚点止损"三项
"""
import json, time, argparse, os, math, sys
import bisect
from collections import Counter
from datetime import datetime, timezone, timedelta

# 🚀 强制行缓冲，确保 GitHub Actions 实时看到输出
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from strategies.loader import load_strategy
from data_fetcher import (
    fetch_and_cache_klines, aggregate_klines,
    calc_ma, calc_ema, calc_atr, calc_rsi, calc_adx,
    calc_macd, calc_kdj, calc_boll, calc_rsi_series,
    detect_rsi_divergence, find_recent_high, find_recent_low,
    get_primary_source, to_hyperliquid_coin, get_hyperliquid_universe,
    calc_vwap,  # 🚀 v10 新增
)
from email_sender import send_html_email

BJT = timezone(timedelta(hours=8))
LEVERAGE = 10

BT_BARS_30M = 5000
BT_BARS_4H = 3000
BT_BARS_1D = 500

# 滑点（按分层）
SLIPPAGE_BY_TIER = {
    "core": 0.0002,
    "satellite": 0.0005,
    "watch": 0.0010,
}

# 资金费率：每 8 小时假设成本/收入 0.01%
FUNDING_PER_8H = 0.0001

TIER_CN = {"core": "核心池", "satellite": "卫星池", "watch": "观察池"}
TIER_ORDER = ["core", "satellite", "watch"]
TIER_ICON = {"core": "🏆", "satellite": "🥇", "watch": "🔬"}
TIER_COLOR = {"core": "#27ae60", "satellite": "#3498db", "watch": "#9b59b6"}

# 🚀 v10 新增：止损来源标签与颜色映射
STOP_SOURCE_LABEL = {
    "structural": "结构位",
    "capital": "资金位",
    "floor": "1.5%保底",
    "fallback": "兜底2%",
    "unknown": "未知",
}
STOP_SOURCE_COLOR = {
    "structural": "#8e44ad",
    "capital": "#2980b9",
    "floor": "#e67e22",
    "fallback": "#7f8c8d",
    "unknown": "#7f8c8d",
}


# ============================================================
# 工具函数
# ============================================================
def parse_date(d):
    if not d:
        return None
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def fmt_ts(ts_ms, fmt="%Y-%m-%d %H:%M"):
    if ts_ms is None:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts_ms / 1000, BJT).strftime(fmt)
    except Exception:
        return "N/A"


def fmt_ts_short(ts_ms):
    return fmt_ts(ts_ms, "%m-%d %H:%M")


def build_market_data_from_slices(symbol, asset_type, window_30m, window_1h,
                                  window_4h, window_1d, data_mode, data_source):
    md = {
        "symbol": symbol, "asset_type": asset_type, "fetch_status": "ok",
        "data_mode": data_mode, "data_source": data_source,
        "current_price": window_30m[-1]["close"] if window_30m else None,
        "funding_rate": None, "funding_rate_raw": None, "funding_percentile": None,
        "open_interest": None, "day_volume": None,
        "klines_30m": window_30m, "klines_1h": window_1h,
        "klines_4h": window_4h, "klines_1d": window_1d,
        "ma10": None, "atr": None, "rsi": None, "adx": None,
        "recent_high": None, "recent_low": None,
        "rsi_1h": None, "kdj_1h": None, "boll_1h": None, "macd_1h": None, "rsi_div_1h": None,
        "ema20_4h": None, "ema50_4h": None, "rsi_4h": None, "macd_4h": None, "trend_4h": None,
        "ema50_1d": None, "rsi_1d": None, "trend_1d": None,
        # 衍生品字段（回测中通常为 None，走 6 分制降级）
        "vwap_30m": None,
        "basis_pct": None,
        "oi_change_pct_1h": None,
        "oi_change_pct_4h": None,
    }

    klines_30m = window_30m
    klines_1h = window_1h
    klines_4h = window_4h
    klines_1d = window_1d

    md["ma10"] = calc_ma(klines_30m, 10)
    md["atr"] = calc_atr(klines_30m, 14)
    md["rsi"] = calc_rsi(klines_30m, 14)
    md["adx"] = calc_adx(klines_30m, 14)
    md["recent_high"] = find_recent_high(klines_30m, 199)
    md["recent_low"] = find_recent_low(klines_30m, 199)

    # 🚀 v10 新增：VWAP 计算（与监控对齐）
    md["vwap_30m"] = calc_vwap(klines_30m, 48)

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
    if md["ema20_4h"] and md["ema50_4h"] and price:
        if md["ema20_4h"] > md["ema50_4h"] and price > md["ema20_4h"]:
            md["trend_4h"] = "up"
        elif md["ema20_4h"] < md["ema50_4h"] and price < md["ema20_4h"]:
            md["trend_4h"] = "down"
        else:
            md["trend_4h"] = "neutral"
    if md["ema50_1d"] and price:
        if price > md["ema50_1d"] * 1.005:
            md["trend_1d"] = "up"
        elif price < md["ema50_1d"] * 0.995:
            md["trend_1d"] = "down"
        else:
            md["trend_1d"] = "neutral"

    return md


def calc_sharpe(returns, risk_free=0.0):
    if not returns or len(returns) < 2:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (avg - risk_free) / std * math.sqrt(252)


def evaluate_strategy(report):
    excess = report["total_return"] - report["benchmark_return"]
    sharpe = report["sharpe"]
    dd = report["max_dd"]
    trades = report["total_trades"]

    if trades < 5:
        return "⚠️ 样本不足", "交易次数少于5次，不具参考性"

    score = 0
    if excess > 20:
        score += 40
    elif excess > 10:
        score += 30
    elif excess > 0:
        score += 20
    elif excess > -10:
        score += 10

    if sharpe > 1.5:
        score += 30
    elif sharpe > 1.0:
        score += 25
    elif sharpe > 0.5:
        score += 15
    elif sharpe > 0:
        score += 5

    if dd < 10:
        score += 20
    elif dd < 20:
        score += 15
    elif dd < 30:
        score += 10
    else:
        score += 5

    if report["profit_factor"] > 2.0:
        score += 10
    elif report["profit_factor"] > 1.5:
        score += 7
    elif report["profit_factor"] > 1.0:
        score += 3

    if score >= 85:
        return "🏆 S级", f"综合得分{score}。极强的盈利能力和风控"
    if score >= 70:
        return "🥇 A级", f"综合得分{score}。稳定跑赢基准"
    if score >= 55:
        return "🥈 B级", f"综合得分{score}。小幅跑赢基准"
    if score >= 40:
        return "🥉 C级", f"综合得分{score}。勉强跑平"
    return "❌ D级", f"综合得分{score}。跑输基准"


def manage_position(pos, kline, ma10, atr):
    """
    用K线的 low/high 判断止损与移动止盈，避免用收盘价判断导致的高估。

    逻辑：
    1. 先判断硬止损（做多用 low，做空用 high）
    2. 再用 high（做多）/ low（做空）判断移动止盈阶段是否升级
    3. 移动止盈升级后，如果同一根 K 线已经触及新止损，则按新止损出场
    4. 最后判断 MA10 离场（🔧 v10 调整：改用 close + 0.3% 缓冲，避免盘中插针扫损）

    返回 (still_hold, exit_price, reason)
    - still_hold=True 表示还在持仓
    - 移动止盈阶段升级不算离场，只更新 pos["stop"] 和 pos["tp_stage"]
    """
    direction = pos["direction"]
    entry = pos["entry"]
    stop = pos["stop"]
    tp_stage = pos["tp_stage"]

    high = kline["high"]
    low = kline["low"]
    close = kline["close"]  # 🔧 v10 新增：用于 MA10 离场判断

    if direction.startswith("long"):
        # 1. 硬止损（用最低价）
        if low <= stop:
            return False, stop, "止损"

        # 2. 移动止盈阶段升级（用最高价）
        profit_atr = (high - entry) / atr if atr and atr > 0 else 0
        old_stop = pos["stop"]
        if tp_stage < 1 and profit_atr >= 1.0:
            pos["stop"] = entry
            pos["tp_stage"] = 1
            pos["_tp_upgraded"] = True
        elif tp_stage < 2 and profit_atr >= 2.0:
            pos["stop"] = entry + 1.0 * atr
            pos["tp_stage"] = 2
            pos["_tp_upgraded"] = True
        elif tp_stage < 3 and profit_atr >= 3.0:
            pos["stop"] = entry + 2.0 * atr
            pos["tp_stage"] = 3
            pos["_tp_upgraded"] = True

        # 3. 移动止盈升级后，同一根K线若已跌破新止损，按新止损出场
        if pos["stop"] > old_stop and low <= pos["stop"]:
            return False, pos["stop"], "移动止盈回落"

        # 4. MA10 离场（🔧 v10 调整：改用 close + 0.3% 缓冲，避免盘中插针扫损）
        if ma10 and close < ma10 * 0.997:
            return False, close, "MA10跌破"
    else:
        # 做空
        if high >= stop:
            return False, stop, "止损"

        profit_atr = (entry - low) / atr if atr and atr > 0 else 0
        old_stop = pos["stop"]
        if tp_stage < 1 and profit_atr >= 1.0:
            pos["stop"] = entry
            pos["tp_stage"] = 1
            pos["_tp_upgraded"] = True
        elif tp_stage < 2 and profit_atr >= 2.0:
            pos["stop"] = entry - 1.0 * atr
            pos["tp_stage"] = 2
            pos["_tp_upgraded"] = True
        elif tp_stage < 3 and profit_atr >= 3.0:
            pos["stop"] = entry - 2.0 * atr
            pos["tp_stage"] = 3
            pos["_tp_upgraded"] = True

        if pos["stop"] < old_stop and high >= pos["stop"]:
            return False, pos["stop"], "移动止盈回落"

        # 🔧 v10 调整：改用 close + 0.3% 缓冲
        if ma10 and close > ma10 * 1.003:
            return False, close, "MA10突破"

    return True, None, None


def _calc_risk_stats(trades):
    """计算止损空间的统计摘要，供报告使用。"""
    risks = sorted([t["risk_pct"] for t in trades if t.get("risk_pct") is not None])
    if not risks:
        return {}
    n = len(risks)
    return {
        "min": round(risks[0], 2),
        "median": round(risks[n // 2], 2),
        "avg": round(sum(risks) / n, 2),
        "p90": round(risks[min(int(n * 0.9), n - 1)], 2),
        "p95": round(risks[min(int(n * 0.95), n - 1)], 2),
        "p99": round(risks[min(int(n * 0.99), n - 1)], 2),
        "max": round(risks[-1], 2),
    }


def _calc_fill_stats(trades):
    """🚀 v10 新增：统计挂单成交情况。"""
    if not trades:
        return {}
    planned = sum(t.get("planned_stages", 0) for t in trades)
    filled = sum(t.get("filled_stages", 0) for t in trades)
    full = sum(1 for t in trades if t.get("filled_stages", 0) == t.get("planned_stages", 0))
    partial = sum(1 for t in trades if 0 < t.get("filled_stages", 0) < t.get("planned_stages", 0))
    return {
        "planned_stages_total": planned,
        "filled_stages_total": filled,
        "full_fill_trades": full,
        "partial_fill_trades": partial,
        "fill_rate": round(filled / planned * 100, 1) if planned > 0 else 0,
    }


def _check_stage_filled(stage, direction, kline):
    """
    🚀 v10 新增：判断单个挂单档位是否在给定 K 线内成交。
    - 市价单：必然成交
    - 限价单：做多时 low <= 挂单价；做空时 high >= 挂单价
    """
    if stage.get("type") == "market":
        return True
    price = stage.get("price")
    if price is None:
        return False
    if direction.startswith("long"):
        return kline["low"] <= price
    else:
        return kline["high"] >= price


def _simulate_fill(entry_plan, direction, kline, cp):
    """
    🚀 v10 新增：模拟一根 K 线内的挂单成交。
    返回 (filled_stages, actual_entry_price, actual_weight_pct) 或 (None, None, None) 表示完全没成交。
    - filled_stages: 成交的档位列表
    - actual_entry_price: 按实际成交权重算的加权入场价
    - actual_weight_pct: 实际成交权重占计划总权重的比例（0~1）
    """
    stages = entry_plan.get("stages", [])
    if not stages:
        return None, None, None

    planned_total_weight = sum(s["weight"] for s in stages)
    if planned_total_weight <= 0:
        return None, None, None

    filled = []
    for s in stages:
        if _check_stage_filled(s, direction, kline):
            filled.append(s)

    if not filled:
        return None, None, None

    filled_weight = sum(s["weight"] for s in filled)
    if filled_weight <= 0:
        return None, None, None

    actual_entry = sum(s["price"] * s["weight"] for s in filled) / filled_weight
    actual_weight_pct = filled_weight / planned_total_weight

    return filled, actual_entry, actual_weight_pct


def _print_exit_stats(symbol, trades, exit_reasons, tp_upgrade_count):
    """
    打印本标的的离场原因分布 + 止损空间分布。
    🚀 v10 新增：止损来源分布 + 挂单成交分布
    """
    total = len(trades)

    print(f"   📊 离场原因分布（{symbol}）：", flush=True)
    if total == 0:
        print(f"      （本区间无交易）", flush=True)
        return

    # ---------- 离场原因 ----------
    order = ["止损", "移动止盈回落", "MA10跌破", "MA10突破", "末尾平仓"]
    for k in order:
        cnt = exit_reasons.get(k, 0)
        if cnt == 0:
            continue
        pct = cnt / total * 100
        bar = "█" * int(pct / 5)
        print(f"      {k:<10} {cnt:>4} 笔 ({pct:>5.1f}%) {bar}", flush=True)

    for k, cnt in exit_reasons.items():
        if k not in order and cnt > 0:
            pct = cnt / total * 100
            print(f"      {k:<10} {cnt:>4} 笔 ({pct:>5.1f}%)", flush=True)

    print(f"      —— 移动止盈升级次数：{tp_upgrade_count}", flush=True)
    print(f"      —— 平均持仓：{sum(t['bars_held'] for t in trades) / total:.1f} 根 30m", flush=True)

    # 🚀 v10 新增：止损来源分布
    source_counter = Counter(t.get("stop_source", "unknown") for t in trades)
    if source_counter:
        src_label = {
            "structural": "结构位",
            "capital": "资金位",
            "floor": "1.5%保底",
            "fallback": "兜底2%",
            "unknown": "未知",
        }
        print(f"   🎯 止损来源分布：", flush=True)
        for k, cnt in source_counter.most_common():
            pct = cnt / total * 100
            label = src_label.get(k, k)
            print(f"      {label:<10} {cnt:>4} 笔 ({pct:>5.1f}%)", flush=True)

    # 🚀 v10 新增：挂单成交分布
    fill_stats = _calc_fill_stats(trades)
    if fill_stats:
        print(f"   📦 挂单成交分布：", flush=True)
        print(f"      计划档位 {fill_stats['planned_stages_total']} 个 "
              f"｜ 实际成交 {fill_stats['filled_stages_total']} 个 "
              f"｜ 成交率 {fill_stats['fill_rate']}%", flush=True)
        print(f"      全部成交 {fill_stats['full_fill_trades']} 笔 ｜ "
              f"部分成交 {fill_stats['partial_fill_trades']} 笔", flush=True)

    # ---------- 止损空间分布 ----------
    risks = sorted([t["risk_pct"] for t in trades if t.get("risk_pct") is not None])
    if not risks:
        return

    n = len(risks)
    avg_r = sum(risks) / n
    median_r = risks[n // 2]
    p90 = risks[min(int(n * 0.9), n - 1)]
    p95 = risks[min(int(n * 0.95), n - 1)]
    p99 = risks[min(int(n * 0.99), n - 1)]
    max_r = risks[-1]
    min_r = risks[0]

    print(f"   📏 止损空间分布（entry→stop 距离 %）：", flush=True)
    print(f"      最小 {min_r:>6.2f}% ｜ 中位 {median_r:>6.2f}% ｜ 平均 {avg_r:>6.2f}%", flush=True)
    print(f"      P90 {p90:>6.2f}% ｜ P95 {p95:>6.2f}% ｜ P99 {p99:>6.2f}% ｜ 最大 {max_r:>6.2f}%", flush=True)

    # 分桶直方图
    buckets = [
        (0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0),
        (5.0, 7.5), (7.5, 10.0), (10.0, 15.0), (15.0, 20.0), (20.0, 999.0),
    ]
    print(f"      分桶：", flush=True)
    for lo, hi in buckets:
        cnt = sum(1 for r in risks if lo <= r < hi)
        if cnt == 0:
            continue
        pct = cnt / n * 100
        bar = "█" * int(pct / 3)
        label = f"{lo:g}-{hi:g}%" if hi < 999 else f">{lo:g}%"
        print(f"        {label:<10} {cnt:>4} 笔 ({pct:>5.1f}%) {bar}", flush=True)

    # 止损空间 → 仓位换算
    print(f"      仓位换算（2% 本金 ÷ 止损空间，上限 50%）：", flush=True)
    for pctl, val in [("最小", min_r), ("中位", median_r), ("P95", p95), ("最大", max_r)]:
        if val <= 0:
            continue
        pos_pct = min(0.5, 0.02 / (val / 100)) * 100
        print(f"        {pctl:<3} {val:>6.2f}%  →  仓位 {pos_pct:>5.1f}%", flush=True)


# ============================================================
# 核心回测逻辑（含滑点+资金费率）
# ============================================================
def run_single(strategy_name, symbol, asset_type, capital, fee, start_ms, end_ms, tier="satellite"):
    print(f"\n▶️  回测 [{TIER_CN.get(tier, tier)}] {strategy_name} | {symbol}", flush=True)

    slippage_rate = SLIPPAGE_BY_TIER.get(tier, 0.0005)

    klines_30m, info_30m = fetch_and_cache_klines(symbol, asset_type, "30m", BT_BARS_30M)
    if not klines_30m:
        print(f"⚠️  {symbol} 30m 数据获取失败", flush=True)
        return None
    klines_4h, info_4h = fetch_and_cache_klines(symbol, asset_type, "4h", BT_BARS_4H)

    klines_1h = aggregate_klines(klines_30m, 2)
    if not klines_4h:
        klines_4h = aggregate_klines(klines_30m, 8)

    klines_1d_direct, _ = fetch_and_cache_klines(symbol, asset_type, "1d", BT_BARS_1D)
    klines_1d = klines_1d_direct if klines_1d_direct else []
    if len(klines_1d) < 50:
        klines_1d_agg = aggregate_klines(klines_4h, 6)
        if len(klines_1d_agg) > len(klines_1d):
            klines_1d = klines_1d_agg

    if len(klines_30m) < 250:
        print(f"⚠️  {symbol} 30m 数据不足（{len(klines_30m)}根），至少需要250根", flush=True)
        return None

    ts_1h = [k["timestamp"] for k in klines_1h]
    ts_4h = [k["timestamp"] for k in klines_4h]
    ts_1d = [k["timestamp"] for k in klines_1d]
    ts_30m = [k["timestamp"] for k in klines_30m]

    warmup = 200
    start_idx = warmup
    if start_ms:
        idx = bisect.bisect_left(ts_30m, start_ms)
        start_idx = max(warmup, idx)
    end_idx = len(klines_30m) - 1
    if end_ms:
        idx = bisect.bisect_right(ts_30m, end_ms) - 1
        end_idx = min(end_idx, idx)

    if start_idx >= end_idx:
        print(f"⚠️  {symbol} 回放范围无效", flush=True)
        return None

    backtest_start_ms = ts_30m[start_idx]
    backtest_end_ms = ts_30m[end_idx]
    print(f"   数据源：{info_30m['actual']} | 30m:{len(klines_30m)}根 | 4h:{len(klines_4h)}根 | 1d:{len(klines_1d)}根", flush=True)
    print(f"   回放区间：{fmt_ts(backtest_start_ms)} ~ {fmt_ts(backtest_end_ms)}", flush=True)
    print(f"   滑点：{slippage_rate*100:.3f}% / 费率：{FUNDING_PER_8H*100:.3f}%/8h（做空收入，做多成本）", flush=True)

    strategy = load_strategy(strategy_name)
    data_mode = "futures" if info_30m["primary"] == "hyperliquid" else "spot"
    data_source = "Hyperliquid 合约" if data_mode == "futures" else "现货"

    trades = []
    position = None
    pos_info = None
    current_cap_theoretical = capital
    current_cap_real = capital

    # 离场原因统计
    exit_reasons = Counter()
    tp_upgrade_count = 0
    # 🚀 v10 新增：因未成交而跳过的信号数
    skipped_signals = 0

    for i in range(start_idx, end_idx + 1):
        current_ts = ts_30m[i]
        window_30m = klines_30m[:i + 1]

        j1h = bisect.bisect_right(ts_1h, current_ts)
        j4h = bisect.bisect_right(ts_4h, current_ts)
        j1d = bisect.bisect_right(ts_1d, current_ts)
        window_1h = klines_1h[max(0, j1h - 200):j1h]
        window_4h = klines_4h[max(0, j4h - 2000):j4h]
        window_1d = klines_1d[max(0, j1d - 200):j1d]

        md = build_market_data_from_slices(
            symbol, asset_type, window_30m, window_1h,
            window_4h, window_1d, data_mode, data_source
        )
        cp = md["current_price"]
        atr = md["atr"]
        ma10 = md["ma10"]

        # ---------- 平仓处理（用整根K线） ----------
        if position is not None:
            current_kline = klines_30m[i]
            pos_info["_tp_upgraded"] = False
            still_hold, exit_price, reason = manage_position(pos_info, current_kline, ma10, atr)
            if pos_info.get("_tp_upgraded"):
                tp_upgrade_count += 1

            if not still_hold:
                entry = pos_info["entry"]
                direction = pos_info["direction"]
                pnl_pct = (exit_price - entry) / entry if direction.startswith("long") else (entry - exit_price) / entry

                risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
                base_position_value = min(capital * 0.5, capital * 0.02 / risk_pct) if risk_pct > 0 else capital * 0.5
                # 🚀 v10 新增：按实际成交权重调整仓位
                actual_weight_pct = pos_info.get("actual_weight_pct", 1.0)
                position_value = base_position_value * actual_weight_pct

                fee_cost = position_value * fee * 2
                pnl_theoretical = position_value * pnl_pct - fee_cost

                slippage_cost = position_value * slippage_rate * 2

                holding_bars = i - pos_info["entry_idx"]
                holding_hours = holding_bars * 0.5
                settlements = holding_hours / 8
                if direction == "short":
                    funding_usd = -position_value * FUNDING_PER_8H * settlements
                else:
                    funding_usd = position_value * FUNDING_PER_8H * settlements
                pnl_real = pnl_theoretical - slippage_cost - funding_usd

                current_cap_theoretical += pnl_theoretical
                current_cap_real += pnl_real

                margin_10x = position_value / LEVERAGE
                coin_amount = position_value / entry if entry else 0

                exit_reasons[reason] += 1

                trades.append({
                    "entry_idx": pos_info["entry_idx"], "exit_idx": i,
                    "direction": direction,
                    "entry_price": entry, "exit_price": exit_price,
                    "entry_time_ms": ts_30m[pos_info["entry_idx"]],
                    "exit_time_ms": ts_30m[i],
                    "pnl_pct": pnl_pct,
                    "pnl_usd": pnl_theoretical,
                    "pnl_usd_real": pnl_real,
                    "slippage_usd": slippage_cost,
                    "funding_usd": funding_usd,
                    "fee_usd": fee_cost,
                    "bars_held": holding_bars,
                    "exit_reason": reason,
                    "position_value": position_value,
                    "margin_10x": margin_10x,
                    "coin_amount": coin_amount,
                    "stop_price": pos_info["original_stop"],
                    "risk_pct": risk_pct * 100,
                    # 🚀 v10 新增：止损来源 + 成交情况
                    "stop_source": pos_info.get("stop_source", "unknown"),
                    "stop_structural": pos_info.get("stop_structural"),
                    "stop_capital": pos_info.get("stop_capital"),
                    "planned_stages": pos_info.get("planned_stages", 0),
                    "filled_stages": pos_info.get("filled_stages", 0),
                    "actual_weight_pct": actual_weight_pct,
                })
                position = None
                pos_info = None

        # ---------- 开仓判断 ----------
        if position is None:
            try:
                res = strategy.evaluate(symbol, asset_type, md)
            except Exception:
                continue

            if res.get("triggered") and res.get("direction") != "spot_warning":
                entry_plan = res.get("entry_plan")
                # 🚀 v10 修改：改用 stop 判断（不再依赖 avg_price），并模拟成交
                if entry_plan and entry_plan.get("stop"):
                    direction = res["direction"]
                    current_kline = klines_30m[i]

                    # 🚀 v10 新增：模拟当根 K 线内挂单成交情况
                    filled_stages, actual_entry, actual_weight_pct = _simulate_fill(
                        entry_plan, direction, current_kline, cp
                    )

                    if filled_stages is None:
                        # 完全没成交，信号作废
                        skipped_signals += 1
                        continue

                    position = direction
                    pos_info = {
                        "direction": position,
                        "entry": actual_entry,
                        "stop": entry_plan["stop"],
                        "original_stop": entry_plan["stop"],
                        "tp_stage": 0,
                        "entry_idx": i,
                        "_tp_upgraded": False,
                        # 🚀 v10 新增字段
                        "stop_source": entry_plan.get("stop_source", "unknown"),
                        "stop_structural": entry_plan.get("stop_structural"),
                        "stop_capital": entry_plan.get("stop_capital"),
                        "planned_stages": len(entry_plan.get("stages", [])),
                        "filled_stages": len(filled_stages),
                        "actual_weight_pct": actual_weight_pct,
                    }

    # ---------- 末尾强制平仓 ----------
    if position is not None and pos_info:
        cp = klines_30m[end_idx]["close"]
        entry = pos_info["entry"]
        direction = pos_info["direction"]
        pnl_pct = (cp - entry) / entry if direction.startswith("long") else (entry - cp) / entry
        risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
        base_position_value = min(capital * 0.5, capital * 0.02 / risk_pct) if risk_pct > 0 else capital * 0.5
        # 🚀 v10 新增：按实际成交权重调整仓位
        actual_weight_pct = pos_info.get("actual_weight_pct", 1.0)
        position_value = base_position_value * actual_weight_pct

        fee_cost = position_value * fee * 2
        pnl_theoretical = position_value * pnl_pct - fee_cost
        slippage_cost = position_value * slippage_rate * 2
        holding_bars = end_idx - pos_info["entry_idx"]
        holding_hours = holding_bars * 0.5
        settlements = holding_hours / 8
        if direction == "short":
            funding_usd = -position_value * FUNDING_PER_8H * settlements
        else:
            funding_usd = position_value * FUNDING_PER_8H * settlements
        pnl_real = pnl_theoretical - slippage_cost - funding_usd

        current_cap_theoretical += pnl_theoretical
        current_cap_real += pnl_real
        margin_10x = position_value / LEVERAGE
        coin_amount = position_value / entry if entry else 0

        exit_reasons["末尾平仓"] += 1

        trades.append({
            "entry_idx": pos_info["entry_idx"], "exit_idx": end_idx,
            "direction": direction,
            "entry_price": entry, "exit_price": cp,
            "entry_time_ms": ts_30m[pos_info["entry_idx"]],
            "exit_time_ms": ts_30m[end_idx],
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_theoretical,
            "pnl_usd_real": pnl_real,
            "slippage_usd": slippage_cost,
            "funding_usd": funding_usd,
            "fee_usd": fee_cost,
            "bars_held": holding_bars,
            "exit_reason": "末尾平仓",
            "position_value": position_value,
            "margin_10x": margin_10x,
            "coin_amount": coin_amount,
            "stop_price": pos_info["original_stop"],
            "risk_pct": risk_pct * 100,
            # 🚀 v10 新增字段
            "stop_source": pos_info.get("stop_source", "unknown"),
            "stop_structural": pos_info.get("stop_structural"),
            "stop_capital": pos_info.get("stop_capital"),
            "planned_stages": pos_info.get("planned_stages", 0),
            "filled_stages": pos_info.get("filled_stages", 0),
            "actual_weight_pct": actual_weight_pct,
        })

    # ---------- 离场原因 + 止损空间分布日志 ----------
    _print_exit_stats(symbol, trades, exit_reasons, tp_upgrade_count)
    # 🚀 v10 新增：打印跳过的信号数
    if skipped_signals > 0:
        print(f"   ⏭️  因未成交跳过的信号：{skipped_signals} 个", flush=True)

    # ---------- 统计 ----------
    total = len(trades)
    buy_hold = (klines_30m[end_idx]["close"] - klines_30m[start_idx]["close"]) / klines_30m[start_idx]["close"] * 100

    if total == 0:
        print(f"   ⚠️  未触发任何交易", flush=True)
        return {
            "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
            "tier": tier,
            "data_mode": data_mode, "data_source": data_source,
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "final_capital": capital, "total_return": 0, "max_dd": 0,
            "final_capital_real": capital, "total_return_real": 0, "max_dd_real": 0,
            "benchmark_return": round(buy_hold, 2),
            "excess_return": round(-buy_hold, 2),
            "excess_return_real": round(-buy_hold, 2),
            "sharpe": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "avg_bars_held": 0, "rating": "⚠️ 无交易",
            "rating_desc": "策略在回测区间未触发任何信号",
            "long_trades": 0, "short_trades": 0,
            "long_win_rate": 0, "short_win_rate": 0,
            "bars_total": end_idx - start_idx + 1,
            "backtest_start_ms": backtest_start_ms,
            "backtest_end_ms": backtest_end_ms,
            "slippage_rate": slippage_rate,
            "trades": [],
            "exit_reasons": dict(exit_reasons),
            "tp_upgrade_count": tp_upgrade_count,
            "risk_stats": {},
            # 🚀 v10 新增
            "fill_stats": {},
            "skipped_signals": skipped_signals,
        }

    # 理论统计
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    win_rate = len(wins) / total * 100
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t["pnl_pct"] for t in wins) / sum(t["pnl_pct"] for t in losses)) if losses else 999
    avg_bars_held = sum(t["bars_held"] for t in trades) / total
    returns_seq = [t["pnl_pct"] for t in trades]
    sharpe = calc_sharpe(returns_seq)

    long_trades = [t for t in trades if t["direction"].startswith("long")]
    short_trades = [t for t in trades if t["direction"] == "short"]
    long_win_rate = len([t for t in long_trades if t["pnl_usd"] > 0]) / len(long_trades) * 100 if long_trades else 0
    short_win_rate = len([t for t in short_trades if t["pnl_usd"] > 0]) / len(short_trades) * 100 if short_trades else 0

    # 理论资金曲线
    cap = capital
    peak = cap
    max_dd = 0
    for t in trades:
        cap += t["pnl_usd"]
        if cap > peak:
            peak = cap
        dd = (peak - cap) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # 实盘资金曲线
    cap_r = capital
    peak_r = cap_r
    max_dd_r = 0
    for t in trades:
        cap_r += t["pnl_usd_real"]
        if cap_r > peak_r:
            peak_r = cap_r
        dd = (peak_r - cap_r) / peak_r if peak_r > 0 else 0
        if dd > max_dd_r:
            max_dd_r = dd

    total_return = (current_cap_theoretical - capital) / capital * 100
    total_return_real = (current_cap_real - capital) / capital * 100

    report = {
        "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
        "tier": tier,
        "data_mode": data_mode, "data_source": data_source,
        "total_trades": total, "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "final_capital": round(current_cap_theoretical, 2),
        "total_return": round(total_return, 2),
        "max_dd": round(max_dd * 100, 2),
        "final_capital_real": round(current_cap_real, 2),
        "total_return_real": round(total_return_real, 2),
        "max_dd_real": round(max_dd_r * 100, 2),
        "benchmark_return": round(buy_hold, 2),
        "excess_return": round(total_return - buy_hold, 2),
        "excess_return_real": round(total_return_real - buy_hold, 2),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win * 100, 2),
        "avg_loss": round(avg_loss * 100, 2),
        "avg_bars_held": round(avg_bars_held, 1),
        "long_trades": len(long_trades), "short_trades": len(short_trades),
        "long_win_rate": round(long_win_rate, 1),
        "short_win_rate": round(short_win_rate, 1),
        "bars_total": end_idx - start_idx + 1,
        "backtest_start_ms": backtest_start_ms,
        "backtest_end_ms": backtest_end_ms,
        "slippage_rate": slippage_rate,
        "trades": trades,
        "exit_reasons": dict(exit_reasons),
        "tp_upgrade_count": tp_upgrade_count,
        "risk_stats": _calc_risk_stats(trades),
        # 🚀 v10 新增
        "fill_stats": _calc_fill_stats(trades),
        "skipped_signals": skipped_signals,
    }

    rating, rating_desc = evaluate_strategy(report)
    report["rating"] = rating
    report["rating_desc"] = rating_desc

    print(f"   ✅ {total}笔 | 胜率{win_rate:.1f}% | "
          f"理论{total_return:.2f}% → 实盘{total_return_real:.2f}% | "
          f"基准{buy_hold:.2f}% | {rating}", flush=True)
    return report


# ============================================================
# 标的读取（从 config.json 的 backtest_tiers / backtest_watchlist）
# ============================================================
def resolve_backtest_symbols(cfg, cli_symbols=None):
    if cli_symbols:
        return cli_symbols, {s: "satellite" for s in cli_symbols}

    tiers = cfg.get("backtest_tiers", {})
    symbol_list = []
    tier_map = {}

    if tiers:
        for t in TIER_ORDER:
            for sym in tiers.get(t, []):
                if sym not in tier_map:
                    symbol_list.append(sym)
                    tier_map[sym] = t
        return symbol_list, tier_map

    bt_list = cfg.get("backtest_watchlist", [])
    if bt_list:
        return bt_list, {s: "satellite" for s in bt_list}

    d = cfg.get("backtest_defaults", {})
    fallback = d.get("symbols", ["BTC_USDT"])
    return fallback, {s: "satellite" for s in fallback}


# ============================================================
# HTML 渲染
# ============================================================
def _render_trade_card(t, idx, show_real=True):
    is_win = t["pnl_usd"] > 0
    is_long = t["direction"].startswith("long")

    accent = "#27ae60" if is_win else "#c0392b"
    bg = "#f0faf3" if is_win else "#fdf0f0"
    dir_color = "#27ae60" if is_long else "#c0392b"
    dir_label = "做多" if is_long else "做空"
    dir_arrow = "⬆" if is_long else "⬇"

    pnl_pct = t["pnl_pct"] * 100
    pnl_pct_str = f"+{pnl_pct:.2f}%" if pnl_pct > 0 else f"{pnl_pct:.2f}%"
    pnl_usd = t["pnl_usd"]
    pnl_usd_str = f"+${pnl_usd:.2f}" if pnl_usd > 0 else f"-${abs(pnl_usd):.2f}"

    pnl_real = t.get("pnl_usd_real", pnl_usd)
    pnl_real_str = f"+${pnl_real:.2f}" if pnl_real > 0 else f"-${abs(pnl_real):.2f}"

    entry_time = fmt_ts_short(t.get("entry_time_ms"))
    exit_time = fmt_ts_short(t.get("exit_time_ms"))
    pos_val = t.get("position_value", 0)
    margin = t.get("margin_10x", 0)
    coin_amt = t.get("coin_amount", 0)
    stop_price = t.get("stop_price", 0)
    slip = t.get("slippage_usd", 0)
    fund = t.get("funding_usd", 0)

    # 🚀 v10 新增：止损来源
    stop_source = t.get("stop_source", "unknown")
    source_label = STOP_SOURCE_LABEL.get(stop_source, "未知")
    source_color = STOP_SOURCE_COLOR.get(stop_source, "#7f8c8d")

    # 🚀 v10 新增：成交情况
    planned = t.get("planned_stages", 0)
    filled = t.get("filled_stages", 0)
    actual_w = t.get("actual_weight_pct", 1.0)
    if planned > 0 and filled > 0:
        if filled == planned:
            fill_label = f"全成 {filled}/{planned}"
            fill_color = "#27ae60"
        else:
            fill_label = f"部分成交 {filled}/{planned}（实际仓位 {actual_w*100:.0f}%）"
            fill_color = "#e67e22"
    else:
        fill_label = ""
        fill_color = "#888"

    html = (
        f"<div style='border-left:5px solid {accent}; background:{bg}; "
        f"padding:12px 15px; margin-bottom:10px; border-radius:0 6px 6px 0;'>"
    )

    html += "<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
    html += "<div style='display:flex; align-items:center; gap:10px;'>"
    html += f"<span style='background:#2c3e50; color:#fff; padding:2px 10px; border-radius:4px; font-weight:bold; font-size:13px;'>#{idx}</span>"
    html += f"<span style='background:{dir_color}; color:#fff; padding:2px 10px; border-radius:4px; font-size:12px; font-weight:bold;'>{dir_arrow} {dir_label}</span>"
    html += f"<span style='color:#666; font-size:13px;'>{entry_time} → {exit_time}</span>"
    html += "</div>"
    html += f"<div style='font-size:20px; font-weight:900; color:{accent}; letter-spacing:0.5px;'>{pnl_pct_str}</div>"
    html += "</div>"

    html += "<div style='margin-top:8px; font-size:13px; color:#333;'>"
    html += f"<span style='color:#888;'>入场</span> <b>${t['entry_price']:.4f}</b>"
    html += f" <span style='color:#888;'>→</span> "
    html += f"<span style='color:#888;'>出场</span> <b>${t['exit_price']:.4f}</b>"
    html += f" <span style='color:#888;'>|</span> "
    html += f"<span style='color:#888;'>硬止损</span> <b style='color:#c0392b;'>${stop_price:.4f}</b>"
    # 🚀 v10 新增：止损来源标签
    html += f" <span style='background:{source_color}; color:#fff; padding:1px 6px; border-radius:3px; font-size:11px; font-weight:bold; margin-left:4px;'>{source_label}</span>"
    html += "</div>"

    html += "<div style='margin-top:6px; font-size:12px; color:#888; line-height:1.6;'>"
    html += f"📦 仓位 <b style='color:#555;'>${pos_val:.0f}</b>（10x 保证金 <b style='color:#555;'>${margin:.0f}</b>，{coin_amt:.4f} 个）"
    html += f" &nbsp;·&nbsp; ⏱ 持仓 <b style='color:#555;'>{t['bars_held']}</b> 根"
    html += f" &nbsp;·&nbsp; 🚪 {t.get('exit_reason', '')}"
    # 🚀 v10 新增：部分成交提示
    if fill_label:
        html += f"<br>📊 <span style='color:{fill_color};font-weight:bold;'>{fill_label}</span>"
    html += "</div>"

    if show_real:
        html += "<div style='margin-top:6px; font-size:12px; color:#666; line-height:1.6; background:#fff; padding:6px 10px; border-radius:4px; border-left:3px solid #95a5a6;'>"
        html += f"💵 <b>理论盈亏</b>（扣手续费）：<span style='color:{accent};font-weight:bold;'>{pnl_usd_str}</span>"
        html += f" &nbsp;|&nbsp; 滑点 <b style='color:#c0392b;'>-${slip:.2f}</b>"
        if fund > 0:
            html += f" &nbsp;|&nbsp; 资金费率 <b style='color:#c0392b;'>-${fund:.2f}</b>"
        elif fund < 0:
            html += f" &nbsp;|&nbsp; 资金费率 <b style='color:#27ae60;'>+${abs(fund):.2f}</b>"
        html += f" &nbsp;|&nbsp; <b>实盘预估</b>：<span style='color:{accent};font-weight:bold;'>{pnl_real_str}</span>"
        html += "</div>"

    html += "</div>"
    return html


def _render_trades_cards(trades, show_real=True):
    if not trades:
        return ""
    sorted_trades = sorted(trades, key=lambda t: t.get("entry_time_ms", 0))
    html = "".join(_render_trade_card(t, i, show_real=show_real) for i, t in enumerate(sorted_trades, 1))
    # 🚀 v10 修改：图例里加"止损来源"和"挂单成交"
    html += (
        "<p style='color:#888; font-size:11px; margin:10px 0 0 0; line-height:1.6;'>"
        "💡 <b>时间</b>：北京时间（BJT, UTC+8）<br>"
        "💡 <b>理论盈亏</b>：扣开仓+平仓双边手续费<br>"
        "💡 <b>实盘预估</b>：再扣滑点 + 资金费率（做空收取，做多支付）<br>"
        "💡 <b>离场原因</b>：<b>止损</b>=触及硬止损｜<b>移动止盈回落</b>=移动止盈升级后同一根K线回落触发｜"
        "<b>MA10跌破/突破</b>=动态离场｜<b>末尾平仓</b>=区间结束时强制平仓<br>"
        "💡 <b>止损来源</b>：<span style='color:#8e44ad;'>结构位</span>=形态失效点｜"
        "<span style='color:#2980b9;'>资金位</span>=按ATR计算的资金保护｜"
        "<span style='color:#e67e22;'>1.5%保底</span>=双锚点均过近时强制启用｜"
        "<span style='color:#7f8c8d;'>兜底2%</span>=异常情况<br>"
        "💡 <b>挂单成交</b>：只有当根K线触及挂单价才成交；未触及的档位不成交；"
        "部分成交时按实际权重计算仓位"
        "</p>"
    )
    return html


def _render_consistency_notice(fee, capital):
    # 🚀 v10 修改：口径说明新增"挂单成交"、"部分成交"、"双锚点止损"、"1.5%保底"、"衍生品数据"五行
    return f"""
<div style="background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); padding:18px; border-radius:10px; margin:20px 0; border-left:5px solid #1976d2; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
    <h3 style="margin:0 0 12px 0; color:#0d47a1; font-size:17px;">⚖️ 回测口径说明</h3>
    <table style="width:100%; font-size:13px; border-collapse:collapse;">
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; width:170px; color:#0d47a1; font-weight:bold;">手续费</td>
            <td style="padding:6px 8px;">✅ 双边 <b>{fee*200:.3f}%</b>（开仓+平仓各 {fee*100:.3f}%）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">滑点</td>
            <td style="padding:6px 8px;">✅ 分层：核心 <b>0.02%</b>｜卫星 <b>0.05%</b>｜观察 <b>0.10%</b>（单边）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">资金费率</td>
            <td style="padding:6px 8px;">✅ 做空收取 <b>0.01% / 8h</b>；做多支付 <b>0.01% / 8h</b></td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">挂单成交</td>
            <td style="padding:6px 8px;">✅ 只有当根K线触及挂单价才成交；未成交档位不计入；<b>完全未成交的信号直接作废</b></td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">部分成交</td>
            <td style="padding:6px 8px;">✅ 按实际成交权重计算仓位（不补齐到计划仓位）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">止损机制</td>
            <td style="padding:6px 8px;">✅ 双锚点：结构位（形态失效点） + 资金位（ATR 保护），取更早触发的</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">1.5% 保底</td>
            <td style="padding:6px 8px;">✅ 双锚点均过近时，强制启用最小止损距离</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">移动止盈</td>
            <td style="padding:6px 8px;">✅ 1×ATR 保本 → 2×ATR 上移 → 3×ATR 继续上移；同一根K线回落即出场</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">MA10 离场</td>
            <td style="padding:6px 8px;">✅ 价格盘中跌破/突破 MA10 即离场（按 MA10 价成交）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">衍生品数据</td>
            <td style="padding:6px 8px;">⚠️ 回测含 VWAP；<b>不含 OI 变化和基差</b>（Hyperliquid 无历史数据）</td>
        </tr>
        <tr>
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">仓位基数</td>
            <td style="padding:6px 8px;">✅ 固定本金 <b>{capital}U</b>，单笔风险 2%（按实际成交权重调整）</td>
        </tr>
    </table>
    <p style="margin:12px 0 0 0; padding:8px; background:#fff3cd; border-radius:6px; font-size:13px; color:#856404;">
    🎯 <b>看什么</b>：请优先看「<b>实盘预估收益</b>」列。同时注意「<b>信号作废数</b>」（挂单未成交的次数）和「<b>部分成交</b>」（仓位变轻）的比例。
    </p>
</div>
"""


def _render_single_detail(r, fee=0.0005, capital=10000):
    tier = r.get("tier", "satellite")
    tier_cn = TIER_CN.get(tier, tier)
    tier_icon = TIER_ICON.get(tier, "•")
    tier_color = TIER_COLOR.get(tier, "#333")

    html = "<div style='background:#fff;padding:20px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,0.08);margin-bottom:20px;'>"

    html += "<div style='display:flex; justify-content:space-between; align-items:center; border-bottom:2px dashed #eee; padding-bottom:12px; margin-bottom:15px;'>"
    html += f"<div>"
    html += f"<span style='background:{tier_color};color:#fff;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:bold;margin-right:10px;'>{tier_icon} {tier_cn}</span>"
    html += f"<span style='font-size:22px; font-weight:900; color:#2c3e50;'>{r['symbol'].replace('_USDT','')}</span>"
    html += f"<span style='margin-left:12px; font-size:15px; color:#e67e22; font-weight:bold;'>{r['rating']}</span>"
    html += f"</div>"
    html += f"<div style='font-size:13px; color:#888;'>{r['data_source']}</div>"
    html += "</div>"

    bs = r.get("backtest_start_ms")
    be = r.get("backtest_end_ms")
    if bs and be:
        html += (f"<p style='color:#555;font-size:13px;margin:0 0 12px 0;'>"
                 f"<b>📅 回测区间：</b>{fmt_ts(bs)} ~ {fmt_ts(be)}"
                 f"（共 {r['bars_total']} 根 30m）</p>")

    html += f"<p style='color:#666;font-size:13px;margin:0 0 15px 0;'>{r['rating_desc']}</p>"

    if r["total_trades"] == 0:
        html += f"<p style='color:#999;padding:15px;background:#f8f9fa;border-radius:6px;'>⚠️ 本区间未触发任何交易信号。基准收益：<b>{r['benchmark_return']}%</b></p>"
    else:
        ret_color = "#27ae60" if r["total_return"] > 0 else "#c0392b"
        exc_color = "#27ae60" if r["excess_return"] > 0 else "#c0392b"
        rret_color = "#27ae60" if r["total_return_real"] > 0 else "#c0392b"
        rexc_color = "#27ae60" if r["excess_return_real"] > 0 else "#c0392b"

        html += "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(115px, 1fr)); gap:10px; margin-bottom:15px;'>"

        def metric_box(label, value, color="#2c3e50", is_important=False):
            border = "2px solid #e74c3c" if is_important else "1px solid #eee"
            return (
                f"<div style='background:#fafbfc; padding:10px; border-radius:8px; text-align:center; border:{border};'>"
                f"<div style='color:#888; font-size:11px; margin-bottom:3px;'>{label}</div>"
                f"<div style='color:{color}; font-size:17px; font-weight:bold;'>{value}</div>"
                "</div>"
            )

        html += metric_box("交易次数", r["total_trades"])
        html += metric_box("胜率", f"{r['win_rate']}%")
        html += metric_box("理论收益", f"{r['total_return']}%", ret_color)
        html += metric_box("实盘预估", f"{r['total_return_real']}%", rret_color, is_important=True)
        html += metric_box("基准收益", f"{r['benchmark_return']}%", "#666")
        html += metric_box("理论超额", f"{r['excess_return']}%", exc_color)
        html += metric_box("实盘超额", f"{r['excess_return_real']}%", rexc_color)
        html += metric_box("最大回撤", f"{r['max_dd']}%", "#e67e22")
        html += metric_box("盈亏比", r["profit_factor"])
        html += metric_box("夏普", r["sharpe"])
        html += "</div>"

        html += "<div style='background:#f8f9fa; padding:12px; border-radius:8px; font-size:13px; margin-bottom:15px;'>"
        html += f"<span style='color:#27ae60;'>🟢 做多：<b>{r['long_trades']}</b> 笔（{r['long_win_rate']}%）</span>"
        html += " &nbsp;|&nbsp; "
        html += f"<span style='color:#c0392b;'>🔴 做空：<b>{r['short_trades']}</b> 笔（{r['short_win_rate']}%）</span>"
        html += f" &nbsp;|&nbsp; <span style='color:#666;'>平均持仓 <b>{r['avg_bars_held']}</b> 根30m</span>"
        html += "</div>"

        # 🚀 v10 新增：挂单成交统计
        fs = r.get("fill_stats") or {}
        skipped = r.get("skipped_signals", 0)
        if fs or skipped:
            html += "<div style='background:#f0f8ff; padding:12px; border-radius:8px; font-size:12.5px; margin-bottom:15px; border-left:4px solid #3498db;'>"
            html += "<b>📦 挂单成交统计：</b><br>"
            if fs:
                html += (f"&nbsp;&nbsp;· 计划档位总计：<b>{fs.get('planned_stages_total', 0)}</b> 个 ｜ "
                         f"实际成交：<b>{fs.get('filled_stages_total', 0)}</b> 个 ｜ "
                         f"成交率 <b>{fs.get('fill_rate', 0)}%</b><br>")
                html += (f"&nbsp;&nbsp;· 全部成交：<b>{fs.get('full_fill_trades', 0)}</b> 笔 ｜ "
                         f"部分成交：<b>{fs.get('partial_fill_trades', 0)}</b> 笔<br>")
            if skipped > 0:
                html += (f"&nbsp;&nbsp;· <span style='color:#e67e22;'>因完全未成交作废的信号：<b>{skipped}</b> 个"
                         f"（这些信号在实盘中会被跳过）</span><br>")
            html += "</div>"

        # 离场原因分布
        er = r.get("exit_reasons") or {}
        if er:
            total_e = sum(er.values())
            html += "<div style='background:#fff8e1; padding:12px; border-radius:8px; font-size:12.5px; margin-bottom:15px; border-left:4px solid #f39c12;'>"
            html += "<b>🚪 离场原因分布：</b><br>"
            for k in ["止损", "移动止盈回落", "MA10跌破", "MA10突破", "末尾平仓"]:
                cnt = er.get(k, 0)
                if cnt == 0:
                    continue
                pct = cnt / total_e * 100 if total_e else 0
                html += f"&nbsp;&nbsp;· {k}：<b>{cnt}</b> 笔（{pct:.1f}%）<br>"
            for k, cnt in er.items():
                if k not in ["止损", "移动止盈回落", "MA10跌破", "MA10突破", "末尾平仓"] and cnt > 0:
                    pct = cnt / total_e * 100 if total_e else 0
                    html += f"&nbsp;&nbsp;· {k}：<b>{cnt}</b> 笔（{pct:.1f}%）<br>"
            tp_up = r.get("tp_upgrade_count", 0)
            html += f"&nbsp;&nbsp;· 移动止盈升级次数：<b>{tp_up}</b><br>"
            html += "</div>"

        # 🚀 v10 新增：止损来源分布
        source_counter = Counter(t.get("stop_source", "unknown") for t in r["trades"])
        if source_counter:
            html += "<div style='background:#f4ecf7; padding:12px; border-radius:8px; font-size:12.5px; margin-bottom:15px; border-left:4px solid #8e44ad;'>"
            html += "<b>🎯 止损来源分布：</b><br>"
            for k, cnt in source_counter.most_common():
                pct = cnt / r["total_trades"] * 100 if r["total_trades"] else 0
                label = STOP_SOURCE_LABEL.get(k, "未知")
                color = STOP_SOURCE_COLOR.get(k, "#7f8c8d")
                html += f"&nbsp;&nbsp;· <span style='color:{color};font-weight:bold;'>{label}</span>：<b>{cnt}</b> 笔（{pct:.1f}%）<br>"
            html += "</div>"

        # 止损空间分布
        rs = r.get("risk_stats") or {}
        if rs:
            html += (
                "<div style='background:#eaf2f8; padding:12px; border-radius:8px; "
                "font-size:12.5px; margin-bottom:15px; border-left:4px solid #2980b9;'>"
                "<b>📏 止损空间分布（entry→stop）：</b><br>"
                f"&nbsp;&nbsp;· 最小 <b>{rs.get('min', 0)}%</b> ｜ "
                f"中位 <b>{rs.get('median', 0)}%</b> ｜ "
                f"平均 <b>{rs.get('avg', 0)}%</b><br>"
                f"&nbsp;&nbsp;· P90 <b>{rs.get('p90', 0)}%</b> ｜ "
                f"P95 <b>{rs.get('p95', 0)}%</b> ｜ "
                f"P99 <b>{rs.get('p99', 0)}%</b> ｜ "
                f"<span style='color:#c0392b;font-weight:bold;'>最大 {rs.get('max', 0)}%</span><br>"
                "<span style='color:#888;font-size:11.5px;'>"
                "💡 止损空间越大，2% 规则反推的仓位越小；极端值（>15%）说明该轨道止损挂得过远。"
                "</span>"
                "</div>"
            )

        html += "<h4 style='margin:20px 0 12px 0; color:#2c3e50; font-size:15px;'>📝 交易明细（共 {} 笔）</h4>".format(r["total_trades"])
        html += _render_trades_cards(r["trades"], show_real=True)

    html += "</div>"
    return html


def _render_tier_table(reports, tier):
    if not reports:
        return ""
    tier_icon = TIER_ICON.get(tier, "•")
    tier_cn = TIER_CN.get(tier, tier)
    tier_color = TIER_COLOR.get(tier, "#333")

    sorted_r = sorted(reports, key=lambda x: x["total_return_real"], reverse=True)

    html = f"<h3 style='margin-top:30px; color:{tier_color}; border-left:5px solid {tier_color}; padding-left:10px;'>"
    html += f"{tier_icon} {tier_cn}（{len(reports)} 个标的）</h3>"
    html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,0.08);font-size:13px;border-radius:8px;overflow:hidden;'>"
    html += "<tr style='background:#2c3e50;color:#fff;'>"
    html += "<th style='padding:8px;'>标的</th><th>交易</th><th>胜率</th>"
    html += "<th>理论收益</th><th>实盘预估</th><th>实盘超额</th><th>回撤</th><th>夏普</th><th>评级</th>"
    html += "</tr>"
    for r in sorted_r:
        ret_color = "#27ae60" if r["total_return"] > 0 else "#c0392b"
        rret_color = "#27ae60" if r["total_return_real"] > 0 else "#c0392b"
        rexc_color = "#27ae60" if r["excess_return_real"] > 0 else "#c0392b"
        html += f"<tr style='border-bottom:1px solid #eee;'>"
        html += f"<td style='padding:8px;font-weight:bold;'>{r['symbol'].replace('_USDT','')}</td>"
        html += f"<td style='text-align:center;'>{r['total_trades']}</td>"
        html += f"<td style='text-align:center;'>{r['win_rate']}%</td>"
        html += f"<td style='text-align:center;color:{ret_color};'>{r['total_return']}%</td>"
        html += f"<td style='text-align:center;color:{rret_color};font-weight:bold;'>{r['total_return_real']}%</td>"
        html += f"<td style='text-align:center;color:{rexc_color};'>{r['excess_return_real']}%</td>"
        html += f"<td style='text-align:center;'>{r['max_dd']}%</td>"
        html += f"<td style='text-align:center;'>{r['sharpe']}</td>"
        html += f"<td style='text-align:center;'>{r['rating']}</td>"
        html += "</tr>"
    html += "</table>"
    html += "<p style='color:#888;font-size:11px;margin-top:6px;'>💡 按实盘预估收益降序排列。「实盘预估」已扣除滑点、资金费率，并模拟挂单成交。</p>"
    return html


def build_tiered_backtest_html(all_reports, strategies, period_desc, fee=0.0005, capital=10000):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")

    by_tier = {"core": [], "satellite": [], "watch": []}
    for r in all_reports:
        t = r.get("tier", "satellite")
        if t in by_tier:
            by_tier[t].append(r)

    total = len(all_reports)
    with_trades = [r for r in all_reports if r["total_trades"] > 0]

    html = "<html><body style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Arial,sans-serif;max-width:1200px;margin:auto;padding:20px;background:#f4f6f8;color:#2c3e50;'>"
    html += "<h2 style='border-bottom:3px solid #e74c3c;padding-bottom:12px;'>📊 参谋长分层回测报告</h2>"
    html += f"<p style='color:#666;'><b>生成时间：</b>{now} | <b>请求区间：</b>{period_desc} | <b>策略：</b>{', '.join(strategies)}</p>"

    if all_reports:
        all_starts = [r.get("backtest_start_ms") for r in all_reports if r.get("backtest_start_ms")]
        all_ends = [r.get("backtest_end_ms") for r in all_reports if r.get("backtest_end_ms")]
        if all_starts and all_ends:
            real_start = min(all_starts)
            real_end = max(all_ends)
            days = (real_end - real_start) / 1000 / 86400
            html += f"<p style='color:#666;'><b>📅 实际数据区间：</b>{fmt_ts(real_start)} ~ {fmt_ts(real_end)}（约 {days:.0f} 天）</p>"

    html += _render_consistency_notice(fee, capital)

    html += "<h3 style='margin-top:30px;'>📈 全局总览</h3>"
    if with_trades:
        avg_ret = sum(r["total_return"] for r in with_trades) / len(with_trades)
        avg_ret_real = sum(r["total_return_real"] for r in with_trades) / len(with_trades)
        avg_win = sum(r["win_rate"] for r in with_trades) / len(with_trades)
        total_trades = sum(r["total_trades"] for r in with_trades)
        # 🚀 v10 新增：总信号作废数
        total_skipped = sum(r.get("skipped_signals", 0) for r in all_reports)
    else:
        avg_ret = avg_ret_real = avg_win = total_trades = 0
        total_skipped = 0

    html += "<div style='display:flex;flex-wrap:wrap;gap:12px;margin:15px 0;'>"
    # 🚀 v10 新增：信号作废数卡片
    cards = [
        ("测试标的", total, "#2c3e50"),
        ("核心/卫星/观察", f"{len(by_tier['core'])}/{len(by_tier['satellite'])}/{len(by_tier['watch'])}", "#3498db"),
        ("有交易", len(with_trades), "#27ae60"),
        ("总交易笔数", total_trades, "#3498db"),
        ("信号作废数", total_skipped, "#e67e22"),
        ("平均理论收益", f"{avg_ret:+.2f}%", "#e67e22"),
        ("平均实盘预估", f"{avg_ret_real:+.2f}%", "#27ae60" if avg_ret_real > 0 else "#c0392b"),
        ("平均胜率", f"{avg_win:.1f}%", "#9b59b6"),
    ]
    for label, value, color in cards:
        html += f"<div style='flex:1;min-width:130px;background:#fff;padding:15px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.08);text-align:center;'>"
        html += f"<div style='color:#888;font-size:12px;'>{label}</div>"
        html += f"<div style='color:{color};font-size:22px;font-weight:900;margin-top:6px;'>{value}</div>"
        html += "</div>"
    html += "</div>"

    for tier in TIER_ORDER:
        if by_tier[tier]:
            html += _render_tier_table(by_tier[tier], tier)

    for tier in TIER_ORDER:
        reports_in_tier = sorted(
            [r for r in by_tier[tier] if r["total_trades"] > 0],
            key=lambda x: x["total_return_real"], reverse=True
        )
        if not reports_in_tier:
            continue
        tier_icon = TIER_ICON.get(tier, "•")
        tier_cn = TIER_CN.get(tier, tier)
        tier_color = TIER_COLOR.get(tier, "#333")

        html += f"<h3 style='margin-top:40px; color:{tier_color}; border-left:5px solid {tier_color}; padding-left:10px;'>"
        html += f"🔍 {tier_icon} {tier_cn} · 详细交易明细</h3>"
        detail_n = 5 if tier == "watch" else len(reports_in_tier)
        for r in reports_in_tier[:detail_n]:
            html += _render_single_detail(r, fee=fee, capital=capital)

    html += "<hr style='margin-top:40px;'><p style='color:#aaa;font-size:11px;text-align:center;'>参谋长分层回测报告 · 仅供交流参考，不构成投资建议</p>"
    html += "</body></html>"
    return html


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="*")
    parser.add_argument("--symbols", nargs="*", help="覆盖 config 中的标的（可选）")
    parser.add_argument("--start_date")
    parser.add_argument("--end_date")
    parser.add_argument("--asset_type", default="futures")
    args = parser.parse_args()

    with open("config/config.json") as f:
        cfg = json.load(f)
    d = cfg.get("backtest_defaults", {})
    strategies = args.strategies or os.environ.get("BT_STRATEGIES", "").split() or d.get("strategies", ["v1_default"])
    start_date = args.start_date or os.environ.get("BT_START_DATE") or d.get("start_date", "")
    end_date = args.end_date or os.environ.get("BT_END_DATE") or d.get("end_date", "")
    asset_type = args.asset_type or os.environ.get("BT_ASSET_TYPE") or "futures"
    start_ms = parse_date(start_date)
    end_ms = parse_date(end_date)
    capital = d.get("initial_capital", 10000)
    fee = d.get("fee_rate", 0.0005)

    period_desc = f"{start_date or '全量'} 至 {end_date or '今日'}" if (start_date or end_date) else "全量数据"

    cli_symbols = args.symbols if args.symbols else None
    symbol_list, tier_map = resolve_backtest_symbols(cfg, cli_symbols)

    print(f"\n{'='*70}", flush=True)
    # 🚀 v10 修改：标题版本号
    print(f"📊 参谋长分层回测引擎 v10", flush=True)
    print(f"   策略：{strategies}", flush=True)
    print(f"   标的数：{len(symbol_list)}", flush=True)
    tier_count = {"core": 0, "satellite": 0, "watch": 0}
    for s in symbol_list:
        tier_count[tier_map.get(s, "satellite")] = tier_count.get(tier_map.get(s, "satellite"), 0) + 1
    print(f"   分层：核心 {tier_count.get('core',0)} / 卫星 {tier_count.get('satellite',0)} / 观察 {tier_count.get('watch',0)}", flush=True)
    print(f"   区间：{period_desc}", flush=True)
    print(f"   本金：{capital}U | 手续费：{fee*100}%", flush=True)
    print(f"   数据窗口：30m={BT_BARS_30M}根 / 4h={BT_BARS_4H}根 / 1d={BT_BARS_1D}根", flush=True)
    # 🚀 v10 新增：启动参数说明
    print(f"   挂单成交：模拟当根K线触及才成交", flush=True)
    print(f"   止损机制：双锚点（结构位 / 资金位）+ 1.5%保底", flush=True)
    print(f"{'='*70}\n", flush=True)

    all_reports = []
    t0 = time.time()
    for strat in strategies:
        for i, sym in enumerate(symbol_list, 1):
            tier = tier_map.get(sym, "satellite")
            print(f"\n[{i}/{len(symbol_list)}] {strat} | {sym} [{tier}] | 已用时 {int(time.time()-t0)}s", flush=True)
            rep = run_single(strat, sym, asset_type, capital, fee, start_ms, end_ms, tier=tier)
            if rep:
                all_reports.append(rep)
            time.sleep(0.3)

    if not all_reports:
        print("\n❌ 无有效回测结果", flush=True)
        sys.exit(1)

    print(f"\n{'='*70}", flush=True)
    print(f"✅ 回测完成，总用时 {int(time.time()-t0)}s", flush=True)
    print(f"   有效报告：{len(all_reports)} 份", flush=True)
    with_trades = sum(1 for r in all_reports if r["total_trades"] > 0)
    print(f"   有交易：{with_trades} 份", flush=True)
    print(f"{'='*70}", flush=True)

    html = build_tiered_backtest_html(all_reports, strategies, period_desc, fee=fee, capital=capital)
    now_str = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【参谋长分层回测】{now_str} | {len(all_reports)}标的 | {with_trades}个有交易"

    try:
        send_html_email(subject, html)
        print("\n📧 回测报告已发送", flush=True)
    except Exception as e:
        print(f"\n❌ 邮件发送失败：{e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
