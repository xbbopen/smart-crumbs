# -*- coding: utf-8 -*-
"""
回测引擎 v11 - 形态失效点止损 + 移动止盈

v11 核心改动（相对 v9）：
1. 止损：形态失效点（由策略层计算）
2. 止盈：保本（盈利1R）+ MA10 跟踪
3. 止损单调性：做多只升不降，做空只降不升
4. 离场原因细化：形态失效 / 保本后回落 / MA10跟随离场
"""
import json, time, argparse, os, math, sys
import bisect
from collections import Counter
from datetime import datetime, timezone, timedelta

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
)
from email_sender import send_html_email

BJT = timezone(timedelta(hours=8))
LEVERAGE = 10

BT_BARS_30M = 5000
BT_BARS_4H = 3000
BT_BARS_1D = 500

SLIPPAGE_BY_TIER = {"core": 0.0002, "satellite": 0.0005, "watch": 0.0010}
FUNDING_PER_8H = 0.0001

TIER_CN = {"core": "核心池", "satellite": "卫星池", "watch": "观察池"}
TIER_ORDER = ["core", "satellite", "watch"]
TIER_ICON = {"core": "🏆", "satellite": "🥇", "watch": "🔬"}
TIER_COLOR = {"core": "#27ae60", "satellite": "#3498db", "watch": "#9b59b6"}

EXIT_REASON_CN = {
    "形态失效": "形态失效",
    "保本后回落": "保本后回落",
    "MA10跟随离场": "MA10跟随离场",
    "末尾平仓": "末尾平仓",
}


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
        "vwap_30m": None, "basis_pct": None,
        "oi_change_pct_1h": None, "oi_change_pct_4h": None,
    }

    klines_30m, klines_1h = window_30m, window_1h
    klines_4h, klines_1d = window_4h, window_1d

    md["ma10"] = calc_ma(klines_30m, 10)
    md["atr"] = calc_atr(klines_30m, 14)
    md["rsi"] = calc_rsi(klines_30m, 14)
    md["adx"] = calc_adx(klines_30m, 14)
    md["recent_high"] = find_recent_high(klines_30m, 199)
    md["recent_low"] = find_recent_low(klines_30m, 199)

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
    if excess > 20: score += 40
    elif excess > 10: score += 30
    elif excess > 0: score += 20
    elif excess > -10: score += 10

    if sharpe > 1.5: score += 30
    elif sharpe > 1.0: score += 25
    elif sharpe > 0.5: score += 15
    elif sharpe > 0: score += 5

    if dd < 10: score += 20
    elif dd < 20: score += 15
    elif dd < 30: score += 10
    else: score += 5

    if report["profit_factor"] > 2.0: score += 10
    elif report["profit_factor"] > 1.5: score += 7
    elif report["profit_factor"] > 1.0: score += 3

    if score >= 85: return "🏆 S级", f"综合得分{score}。极强的盈利能力和风控"
    if score >= 70: return "🥇 A级", f"综合得分{score}。稳定跑赢基准"
    if score >= 55: return "🥈 B级", f"综合得分{score}。小幅跑赢基准"
    if score >= 40: return "🥉 C级", f"综合得分{score}。勉强跑平"
    return "❌ D级", f"综合得分{score}。跑输基准"


def manage_position(pos, kline, ma10):
    """
    🔧 v11 移动止盈逻辑：

    阶段 0（未保本）：
      - 止损 = 形态失效点（固定）
      - 如果价格达到"盈利 1R"（R = 初始止损距离）→ 进入阶段 1，止损移到成本价

    阶段 1（保本 + 跟踪）：
      - 止损跟随 MA10（只升不降 / 只降不升）
      - 用 close 判断是否触发止损

    离场原因：
      - "形态失效"：阶段 0 时被初始止损扫
      - "保本后回落"：阶段 1 时收盘回到成本价
      - "MA10跟随离场"：阶段 1 时收盘跌破 MA10 跟踪的止损
    """
    direction = pos["direction"]
    entry = pos["entry"]
    stop = pos["stop"]
    tp_stage = pos["tp_stage"]  # 0=未保本, 1=保本+跟踪
    r_distance = pos.get("r_distance", 0)

    high = kline["high"]
    low = kline["low"]
    close = kline["close"]

    if direction.startswith("long"):
        # 1. 触及止损
        if low <= stop:
            if tp_stage == 0:
                return False, stop, "形态失效"
            else:
                if stop > entry:
                    return False, stop, "MA10跟随离场"
                else:
                    return False, stop, "保本后回落"

        # 2. 保本触发：盈利 1R
        if tp_stage == 0 and r_distance > 0:
            if high >= entry + r_distance:
                pos["stop"] = entry
                pos["tp_stage"] = 1
                pos["_tp_upgraded"] = True

        # 3. 阶段 1：MA10 跟踪（只升不降）
        if pos["tp_stage"] >= 1 and ma10:
            if ma10 > pos["stop"]:
                pos["stop"] = ma10
                pos["_tp_upgraded"] = True

    else:
        # 做空
        if high >= stop:
            if tp_stage == 0:
                return False, stop, "形态失效"
            else:
                if stop < entry:
                    return False, stop, "MA10跟随离场"
                else:
                    return False, stop, "保本后回落"

        if tp_stage == 0 and r_distance > 0:
            if low <= entry - r_distance:
                pos["stop"] = entry
                pos["tp_stage"] = 1
                pos["_tp_upgraded"] = True

        if pos["tp_stage"] >= 1 and ma10:
            if ma10 < pos["stop"]:
                pos["stop"] = ma10
                pos["_tp_upgraded"] = True

    return True, None, None


def _calc_risk_stats(trades):
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


def _print_exit_stats(symbol, trades, exit_reasons, tp_upgrade_count, rejected_signals):
    total = len(trades)

    print(f"   📊 离场原因分布（{symbol}）：", flush=True)
    if total == 0:
        print(f"      （本区间无交易）", flush=True)
        if rejected_signals > 0:
            print(f"      —— 止损过宽被拒信号：{rejected_signals} 个", flush=True)
        return

    order = ["形态失效", "保本后回落", "MA10跟随离场", "末尾平仓"]
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

    print(f"      —— 止损升级次数（保本+跟踪）：{tp_upgrade_count}", flush=True)
    print(f"      —— 平均持仓：{sum(t['bars_held'] for t in trades) / total:.1f} 根 30m", flush=True)
    if rejected_signals > 0:
        print(f"      —— 止损过宽被拒信号：{rejected_signals} 个", flush=True)

    risks = sorted([t["risk_pct"] for t in trades if t.get("risk_pct") is not None])
    if not risks:
        return

    n = len(risks)
    avg_r = sum(risks) / n
    median_r = risks[n // 2]
    max_r = risks[-1]
    min_r = risks[0]

    print(f"   📏 止损空间分布（entry→stop 距离 %）：", flush=True)
    print(f"      最小 {min_r:>6.2f}% ｜ 中位 {median_r:>6.2f}% ｜ 平均 {avg_r:>6.2f}% ｜ 最大 {max_r:>6.2f}%", flush=True)


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
        print(f"⚠️  {symbol} 30m 数据不足（{len(klines_30m)}根）", flush=True)
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

    strategy = load_strategy(strategy_name)
    data_mode = "futures" if info_30m["primary"] == "hyperliquid" else "spot"
    data_source = "Hyperliquid 合约" if data_mode == "futures" else "现货"

    trades = []
    position = None
    pos_info = None
    current_cap_theoretical = capital
    current_cap_real = capital

    exit_reasons = Counter()
    tp_upgrade_count = 0
    rejected_signals = 0  # 🔧 止损过宽被拒信号数

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
        ma10 = md["ma10"]

        # 平仓处理
        if position is not None:
            current_kline = klines_30m[i]
            pos_info["_tp_upgraded"] = False
            still_hold, exit_price, reason = manage_position(pos_info, current_kline, ma10)
            if pos_info.get("_tp_upgraded"):
                tp_upgrade_count += 1

            if not still_hold:
                entry = pos_info["entry"]
                direction = pos_info["direction"]
                pnl_pct = (exit_price - entry) / entry if direction.startswith("long") else (entry - exit_price) / entry

                risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
                position_value = min(capital * 0.5, capital * 0.02 / risk_pct) if risk_pct > 0 else capital * 0.5

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
                    "r_distance": pos_info.get("r_distance", 0),
                    "stop_reason": pos_info.get("stop_reason", ""),
                    "final_stop": pos_info["stop"],  # 平仓时的止损位置
                    "tp_stage_final": pos_info["tp_stage"],
                })
                position = None
                pos_info = None

        # 开仓判断
        if position is None:
            try:
                res = strategy.evaluate(symbol, asset_type, md)
            except Exception:
                continue

            if res.get("triggered") and res.get("direction") != "spot_warning":
                entry_plan = res.get("entry_plan")
                if entry_plan and entry_plan.get("avg_price") and entry_plan.get("stop"):
                    entry_price = entry_plan["avg_price"]
                    stop_price = entry_plan["stop"]
                    position = res["direction"]
                    pos_info = {
                        "direction": position,
                        "entry": entry_price,
                        "stop": stop_price,
                        "original_stop": stop_price,
                        "tp_stage": 0,
                        "entry_idx": i,
                        "_tp_upgraded": False,
                        "r_distance": entry_plan.get("r_distance", 0),
                        "stop_reason": entry_plan.get("stop_reason", ""),
                    }
            else:
                # 检查是否因为止损过宽被拒
                reason = res.get("reason", "")
                if "止损过宽" in reason:
                    rejected_signals += 1

    # 末尾强制平仓
    if position is not None and pos_info:
        cp = klines_30m[end_idx]["close"]
        entry = pos_info["entry"]
        direction = pos_info["direction"]
        pnl_pct = (cp - entry) / entry if direction.startswith("long") else (entry - cp) / entry
        risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
        position_value = min(capital * 0.5, capital * 0.02 / risk_pct) if risk_pct > 0 else capital * 0.5

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
            "r_distance": pos_info.get("r_distance", 0),
            "stop_reason": pos_info.get("stop_reason", ""),
            "final_stop": pos_info["stop"],
            "tp_stage_final": pos_info["tp_stage"],
        })

    _print_exit_stats(symbol, trades, exit_reasons, tp_upgrade_count, rejected_signals)

    total = len(trades)
    buy_hold = (klines_30m[end_idx]["close"] - klines_30m[start_idx]["close"]) / klines_30m[start_idx]["close"] * 100

    if total == 0:
        print(f"   ⚠️  未触发任何交易", flush=True)
        return {
            "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
            "tier": tier, "data_mode": data_mode, "data_source": data_source,
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
            "rejected_signals": rejected_signals,
        }

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

    cap = capital
    peak = cap
    max_dd = 0
    for t in trades:
        cap += t["pnl_usd"]
        if cap > peak: peak = cap
        dd = (peak - cap) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

    cap_r = capital
    peak_r = cap_r
    max_dd_r = 0
    for t in trades:
        cap_r += t["pnl_usd_real"]
        if cap_r > peak_r: peak_r = cap_r
        dd = (peak_r - cap_r) / peak_r if peak_r > 0 else 0
        if dd > max_dd_r: max_dd_r = dd

    total_return = (current_cap_theoretical - capital) / capital * 100
    total_return_real = (current_cap_real - capital) / capital * 100

    report = {
        "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
        "tier": tier, "data_mode": data_mode, "data_source": data_source,
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
        "rejected_signals": rejected_signals,
    }

    rating, rating_desc = evaluate_strategy(report)
    report["rating"] = rating
    report["rating_desc"] = rating_desc

    print(f"   ✅ {total}笔 | 胜率{win_rate:.1f}% | "
          f"理论{total_return:.2f}% → 实盘{total_return_real:.2f}% | "
          f"基准{buy_hold:.2f}% | {rating}", flush=True)
    return report


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
    final_stop = t.get("final_stop", stop_price)
    slip = t.get("slippage_usd", 0)
    fund = t.get("funding_usd", 0)
    stop_reason = t.get("stop_reason", "")
    tp_stage_final = t.get("tp_stage_final", 0)

    # 止损来源：根据最终状态判断
    if tp_stage_final == 0:
        stop_source_label = "形态失效点"
        stop_source_color = "#8e44ad"
    else:
        stop_source_label = "MA10跟踪"
        stop_source_color = "#2980b9"

    html = f"<div style='border-left:5px solid {accent}; background:{bg}; padding:12px 15px; margin-bottom:10px; border-radius:0 6px 6px 0;'>"
    html += "<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
    html += "<div style='display:flex; align-items:center; gap:10px;'>"
    html += f"<span style='background:#2c3e50; color:#fff; padding:2px 10px; border-radius:4px; font-weight:bold; font-size:13px;'>#{idx}</span>"
    html += f"<span style='background:{dir_color}; color:#fff; padding:2px 10px; border-radius:4px; font-size:12px; font-weight:bold;'>{dir_arrow} {dir_label}</span>"
    html += f"<span style='color:#666; font-size:13px;'>{entry_time} → {exit_time}</span>"
    html += "</div>"
    html += f"<div style='font-size:20px; font-weight:900; color:{accent};'>{pnl_pct_str}</div>"
    html += "</div>"

    html += "<div style='margin-top:8px; font-size:13px; color:#333;'>"
    html += f"<span style='color:#888;'>入场</span> <b>${t['entry_price']:.4f}</b> "
    html += f"<span style='color:#888;'>→</span> "
    html += f"<span style='color:#888;'>出场</span> <b>${t['exit_price']:.4f}</b> | "
    html += f"<span style='color:#888;'>初始止损</span> <b style='color:#c0392b;'>${stop_price:.4f}</b> "
    html += f"<span style='background:{stop_source_color}; color:#fff; padding:1px 6px; border-radius:3px; font-size:11px; font-weight:bold; margin-left:4px;'>{stop_source_label}</span>"
    if tp_stage_final > 0:
        html += f" <span style='color:#888;'>｜最终止损</span> <b style='color:#27ae60;'>${final_stop:.4f}</b>"
    html += "</div>"

    if stop_reason:
        html += f"<div style='margin-top:4px; font-size:11.5px; color:#888;'>形态依据：{stop_reason}</div>"

    html += "<div style='margin-top:6px; font-size:12px; color:#888; line-height:1.6;'>"
    html += f"📦 仓位 <b style='color:#555;'>${pos_val:.0f}</b>（10x 保证金 <b style='color:#555;'>${margin:.0f}</b>，{coin_amt:.4f} 个）"
    html += f" &nbsp;·&nbsp; ⏱ 持仓 <b style='color:#555;'>{t['bars_held']}</b> 根"
    html += f" &nbsp;·&nbsp; 🚪 {t.get('exit_reason', '')}"
    if tp_stage_final > 0:
        html += f" &nbsp;·&nbsp; 📈 已保本"
    html += "</div>"

    if show_real:
        html += "<div style='margin-top:6px; font-size:12px; color:#666; line-height:1.6; background:#fff; padding:6px 10px; border-radius:4px; border-left:3px solid #95a5a6;'>"
        html += f"💵 <b>理论盈亏</b>：<span style='color:{accent};font-weight:bold;'>{pnl_usd_str}</span>"
        html += f" | 滑点 <b style='color:#c0392b;'>-${slip:.2f}</b>"
        if fund > 0:
            html += f" | 资金费率 <b style='color:#c0392b;'>-${fund:.2f}</b>"
        elif fund < 0:
            html += f" | 资金费率 <b style='color:#27ae60;'>+${abs(fund):.2f}</b>"
        html += f" | <b>实盘预估</b>：<span style='color:{accent};font-weight:bold;'>{pnl_real_str}</span>"
        html += "</div>"

    html += "</div>"
    return html


def _render_trades_cards(trades, show_real=True):
    if not trades:
        return ""
    sorted_trades = sorted(trades, key=lambda t: t.get("entry_time_ms", 0))
    html = "".join(_render_trade_card(t, i, show_real=show_real) for i, t in enumerate(sorted_trades, 1))
    html += (
        "<p style='color:#888; font-size:11px; margin:10px 0 0 0; line-height:1.6;'>"
        "💡 <b>时间</b>：北京时间（BJT, UTC+8）<br>"
        "💡 <b>止损机制</b>：形态失效点止损（不是资金管理止损）<br>"
        "💡 <b>移动止盈</b>：盈利 1R（R=初始止损距离）后保本，之后跟随 30m MA10<br>"
        "💡 <b>离场原因</b>：<b>形态失效</b>=未保本就被初始止损扫｜"
        "<b>保本后回落</b>=曾保本后收盘回到成本价｜"
        "<b>MA10跟随离场</b>=曾保本，MA10 止损跟随后被触发｜"
        "<b>末尾平仓</b>=区间结束强制平仓"
        "</p>"
    )
    return html


def _render_consistency_notice(fee, capital):
    return f"""
<div style="background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); padding:18px; border-radius:10px; margin:20px 0; border-left:5px solid #1976d2; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
    <h3 style="margin:0 0 12px 0; color:#0d47a1; font-size:17px;">⚖️ 回测口径说明</h3>
    <table style="width:100%; font-size:13px; border-collapse:collapse;">
        <tr style="border-bottom:1px solid #90caf9;"><td style="padding:6px 8px; width:170px; color:#0d47a1; font-weight:bold;">手续费</td><td style="padding:6px 8px;">✅ 双边 <b>{fee*200:.3f}%</b></td></tr>
        <tr style="border-bottom:1px solid #90caf9;"><td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">滑点</td><td style="padding:6px 8px;">✅ 核心 <b>0.02%</b>｜卫星 <b>0.05%</b>｜观察 <b>0.10%</b>（单边）</td></tr>
        <tr style="border-bottom:1px solid #90caf9;"><td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">资金费率</td><td style="padding:6px 8px;">✅ 做空收 0.01%/8h，做多付 0.01%/8h</td></tr>
        <tr style="border-bottom:1px solid #90caf9;"><td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">止损机制</td><td style="padding:6px 8px;">✅ 形态失效点（不是资金管理止损），约束 1.5%~6%，超过 6% 拒绝信号</td></tr>
        <tr style="border-bottom:1px solid #90caf9;"><td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">移动止盈</td><td style="padding:6px 8px;">✅ 盈利 1R 保本 → 30m MA10 跟踪（只升不降 / 只降不升）</td></tr>
        <tr style="border-bottom:1px solid #90caf9;"><td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">离场判断</td><td style="padding:6px 8px;">✅ 用 K 线 low/high 触发止损；用 close 判断 MA10 跟踪</td></tr>
        <tr><td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">仓位基数</td><td style="padding:6px 8px;">✅ 固定本金 <b>{capital}U</b>，单笔风险 2%（受 50% 上限截断）</td></tr>
    </table>
    <p style="margin:12px 0 0 0; padding:8px; background:#fff3cd; border-radius:6px; font-size:13px; color:#856404;">
    🎯 <b>看什么</b>：优先看「实盘预估收益」。<b>止损空间 6% 以上的信号会被直接拒绝</b>。
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
    html += f"<div><span style='background:{tier_color};color:#fff;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:bold;margin-right:10px;'>{tier_icon} {tier_cn}</span>"
    html += f"<span style='font-size:22px; font-weight:900; color:#2c3e50;'>{r['symbol'].replace('_USDT','')}</span>"
    html += f"<span style='margin-left:12px; font-size:15px; color:#e67e22; font-weight:bold;'>{r['rating']}</span></div>"
    html += f"<div style='font-size:13px; color:#888;'>{r['data_source']}</div></div>"

    bs, be = r.get("backtest_start_ms"), r.get("backtest_end_ms")
    if bs and be:
        html += f"<p style='color:#555;font-size:13px;margin:0 0 12px 0;'><b>📅 回测区间：</b>{fmt_ts(bs)} ~ {fmt_ts(be)}（共 {r['bars_total']} 根 30m）</p>"

    html += f"<p style='color:#666;font-size:13px;margin:0 0 15px 0;'>{r['rating_desc']}</p>"

    if r["total_trades"] == 0:
        html += f"<p style='color:#999;padding:15px;background:#f8f9fa;border-radius:6px;'>⚠️ 未触发任何交易。基准收益：<b>{r['benchmark_return']}%</b></p>"
    else:
        ret_color = "#27ae60" if r["total_return"] > 0 else "#c0392b"
        rret_color = "#27ae60" if r["total_return_real"] > 0 else "#c0392b"
        rexc_color = "#27ae60" if r["excess_return_real"] > 0 else "#c0392b"

        html += "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(115px, 1fr)); gap:10px; margin-bottom:15px;'>"

        def metric_box(label, value, color="#2c3e50", is_important=False):
            border = "2px solid #e74c3c" if is_important else "1px solid #eee"
            return (f"<div style='background:#fafbfc; padding:10px; border-radius:8px; text-align:center; border:{border};'>"
                    f"<div style='color:#888; font-size:11px; margin-bottom:3px;'>{label}</div>"
                    f"<div style='color:{color}; font-size:17px; font-weight:bold;'>{value}</div></div>")

        html += metric_box("交易次数", r["total_trades"])
        html += metric_box("胜率", f"{r['win_rate']}%")
        html += metric_box("理论收益", f"{r['total_return']}%", ret_color)
        html += metric_box("实盘预估", f"{r['total_return_real']}%", rret_color, is_important=True)
        html += metric_box("基准收益", f"{r['benchmark_return']}%", "#666")
        html += metric_box("实盘超额", f"{r['excess_return_real']}%", rexc_color)
        html += metric_box("最大回撤", f"{r['max_dd']}%", "#e67e22")
        html += metric_box("盈亏比", r["profit_factor"])
        html += metric_box("夏普", r["sharpe"])
        html += "</div>"

        html += "<div style='background:#f8f9fa; padding:12px; border-radius:8px; font-size:13px; margin-bottom:15px;'>"
        html += f"<span style='color:#27ae60;'>🟢 做多：<b>{r['long_trades']}</b> 笔（{r['long_win_rate']}%）</span> | "
        html += f"<span style='color:#c0392b;'>🔴 做空：<b>{r['short_trades']}</b> 笔（{r['short_win_rate']}%）</span> | "
        html += f"<span style='color:#666;'>平均持仓 <b>{r['avg_bars_held']}</b> 根30m</span>"
        if r.get("rejected_signals", 0) > 0:
            html += f" | <span style='color:#e67e22;'>止损过宽被拒：<b>{r['rejected_signals']}</b> 个信号</span>"
        html += "</div>"

        er = r.get("exit_reasons") or {}
        if er:
            total_e = sum(er.values())
            html += "<div style='background:#fff8e1; padding:12px; border-radius:8px; font-size:12.5px; margin-bottom:15px; border-left:4px solid #f39c12;'>"
            html += "<b>🚪 离场原因分布：</b><br>"
            for k in ["形态失效", "保本后回落", "MA10跟随离场", "末尾平仓"]:
                cnt = er.get(k, 0)
                if cnt == 0: continue
                pct = cnt / total_e * 100 if total_e else 0
                html += f"&nbsp;&nbsp;· {k}：<b>{cnt}</b> 笔（{pct:.1f}%）<br>"
            html += f"&nbsp;&nbsp;· 止损升级次数（保本+跟踪）：<b>{r.get('tp_upgrade_count', 0)}</b><br>"
            html += "</div>"

        rs = r.get("risk_stats") or {}
        if rs:
            html += ("<div style='background:#eaf2f8; padding:12px; border-radius:8px; font-size:12.5px; margin-bottom:15px; border-left:4px solid #2980b9;'>"
                     f"<b>📏 止损空间分布：</b><br>"
                     f"&nbsp;&nbsp;· 最小 <b>{rs.get('min', 0)}%</b> ｜ 中位 <b>{rs.get('median', 0)}%</b> ｜ 平均 <b>{rs.get('avg', 0)}%</b> ｜ 最大 <b>{rs.get('max', 0)}%</b><br>"
                     "<span style='color:#888;font-size:11.5px;'>💡 止损空间已限制在 1.5%~6% 之间；超过 6% 的信号被拒绝。</span>"
                     "</div>")

        html += f"<h4 style='margin:20px 0 12px 0; color:#2c3e50; font-size:15px;'>📝 交易明细（共 {r['total_trades']} 笔）</h4>"
        html += _render_trades_cards(r["trades"], show_real=True)

    html += "</div>"
    return html


def _render_tier_table(reports, tier):
    if not reports: return ""
    tier_icon = TIER_ICON.get(tier, "•")
    tier_cn = TIER_CN.get(tier, tier)
    tier_color = TIER_COLOR.get(tier, "#333")
    sorted_r = sorted(reports, key=lambda x: x["total_return_real"], reverse=True)

    html = f"<h3 style='margin-top:30px; color:{tier_color}; border-left:5px solid {tier_color}; padding-left:10px;'>{tier_icon} {tier_cn}（{len(reports)} 个标的）</h3>"
    html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,0.08);font-size:13px;border-radius:8px;overflow:hidden;'>"
    html += "<tr style='background:#2c3e50;color:#fff;'><th style='padding:8px;'>标的</th><th>交易</th><th>胜率</th><th>理论收益</th><th>实盘预估</th><th>实盘超额</th><th>回撤</th><th>夏普</th><th>评级</th></tr>"
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
        html += f"<td style='text-align:center;'>{r['rating']}</td></tr>"
    html += "</table>"
    html += "<p style='color:#888;font-size:11px;margin-top:6px;'>💡 按实盘预估收益降序排列。</p>"
    return html


def build_tiered_backtest_html(all_reports, strategies, period_desc, fee=0.0005, capital=10000):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    by_tier = {"core": [], "satellite": [], "watch": []}
    for r in all_reports:
        t = r.get("tier", "satellite")
        if t in by_tier: by_tier[t].append(r)

    total = len(all_reports)
    with_trades = [r for r in all_reports if r["total_trades"] > 0]

    html = "<html><body style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Arial,sans-serif;max-width:1200px;margin:auto;padding:20px;background:#f4f6f8;color:#2c3e50;'>"
    html += "<h2 style='border-bottom:3px solid #e74c3c;padding-bottom:12px;'>📊 参谋长分层回测报告 v11</h2>"
    html += f"<p style='color:#666;'><b>生成时间：</b>{now} | <b>请求区间：</b>{period_desc} | <b>策略：</b>{', '.join(strategies)}</p>"

    if all_reports:
        all_starts
