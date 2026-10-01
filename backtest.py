# -*- coding: utf-8 -*-
"""
回测引擎 - 分层回测 + 现实模拟

v7 核心改动：
1. 从 config.json 的 backtest_tiers 读分层标的，不再拉全市场
2. 每笔交易扣滑点（按币种分层：核心 0.02% / 卫星 0.05% / 观察 0.10%）
3. 做空持仓模拟资金费率成本（0.01% / 8h）
4. 报告分三层展示：核心池 / 卫星池 / 观察池
5. 新增"实盘预估收益"列（扣滑点+费率）
6. 无冷却期（按用户要求）

v8 核心改动（策略整改适配）：
7. 适配 v1_default.py 的 global_judgment 结构
8. 轨道3 改为独立稀有事件（不受状态机约束），触发条件由策略内部负责
9. 止损统一基于加权入场价 ± ATR 倍数（含 1.5%~3.5% 双保险），由策略内部负责
10. 波动率熔断（ATR/价格 > 3% 时策略内部自动暂停所有轨道）
11. KDJ 极端值过滤（J>100 否决做多），由策略内部负责
12. 轨道4 加"站上30m MA10"硬条件，由策略内部负责

说明：v8 的策略层改动全部在 v1_default.py 内部，回测代码本身不需要
修改核心逻辑，只保留原有的分层回测 + 现实模拟框架。
"""
import json, time, argparse, os, math, sys
import bisect
from datetime import datetime, timezone, timedelta
from strategies.loader import load_strategy
from data_fetcher import (
    fetch_and_cache_klines, aggregate_klines,
    calc_ma, calc_ema, calc_atr, calc_rsi, calc_adx,
    calc_macd, calc_kdj, calc_boll, calc_rsi_series,
    detect_rsi_divergence, find_recent_high, find_recent_low,
    get_primary_source, to_hyperliquid_coin, get_hyperliquid_universe,
)
from email_sender import send_html_email
# 🚀 强制行缓冲，让 print 立即输出到 GitHub Actions 日志
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

BJT = timezone(timedelta(hours=8))
LEVERAGE = 10       # 回测统一按 10 倍杠杆口径计算保证金

# 各周期目标根数（回测用，比实盘多一些历史）
BT_BARS_30M = 5000
BT_BARS_4H = 3000
BT_BARS_1D = 500

# 滑点（按分层）
SLIPPAGE_BY_TIER = {
    "core": 0.0002,       # 核心池 0.02%
    "satellite": 0.0005,  # 卫星池 0.05%
    "watch": 0.0010,      # 观察池 0.10%
}

# 资金费率：每 8 小时假设成本 0.01%（仅对做空扣）
FUNDING_PER_8H = 0.0001

# 分层中文化
TIER_CN = {"core": "核心池", "satellite": "卫星池", "watch": "观察池"}
TIER_ORDER = ["core", "satellite", "watch"]
TIER_ICON = {"core": "🏆", "satellite": "🥇", "watch": "🔬"}
TIER_COLOR = {"core": "#27ae60", "satellite": "#3498db", "watch": "#9b59b6"}


# ============================================================
# 工具函数
# ============================================================
def parse_date(d):
    if not d: return None
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except:
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


# ============================================================
# 从预切好的多周期K线组装 market_data
# ============================================================
def build_market_data_from_slices(symbol, asset_type, window_30m, window_1h,
                                   window_4h, window_1d, data_mode, data_source):
    """从预切好的多周期K线组装 market_data。"""
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
    """S/A/B/C/D 评级"""
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


# ============================================================
# 持仓管理：硬止损 + 移动止盈阶梯 + MA10 动态离场
# ============================================================
def manage_position(pos, cp, ma10, atr):
    """
    持仓管理：返回 (still_hold, exit_price, exit_reason)
    pos: {"direction": ..., "entry": ..., "stop": ..., "tp_stage": 0/1/2/3, "entry_idx": ...}
    """
    direction = pos["direction"]
    entry = pos["entry"]
    stop = pos["stop"]
    tp_stage = pos["tp_stage"]

    if direction.startswith("long"):
        # 止损
        if cp <= stop:
            return False, stop, "止损"
        # 移动止盈阶梯
        profit_atr = (cp - entry) / atr if atr and atr > 0 else 0
        if tp_stage < 1 and profit_atr >= 1.0:
            pos["stop"] = entry
            pos["tp_stage"] = 1
        elif tp_stage < 2 and profit_atr >= 2.0:
            pos["stop"] = entry + 1.0 * atr
            pos["tp_stage"] = 2
        elif tp_stage < 3 and profit_atr >= 3.0:
            pos["stop"] = entry + 2.0 * atr
            pos["tp_stage"] = 3
        # MA10 离场
        if ma10 and cp < ma10:
            return False, cp, "MA10跌破"
    else:
        # 做空
        if cp >= stop:
            return False, stop, "止损"
        profit_atr = (entry - cp) / atr if atr and atr > 0 else 0
        if tp_stage < 1 and profit_atr >= 1.0:
            pos["stop"] = entry
            pos["tp_stage"] = 1
        elif tp_stage < 2 and profit_atr >= 2.0:
            pos["stop"] = entry - 1.0 * atr
            pos["tp_stage"] = 2
        elif tp_stage < 3 and profit_atr >= 3.0:
            pos["stop"] = entry - 2.0 * atr
            pos["tp_stage"] = 3
        if ma10 and cp > ma10:
            return False, cp, "MA10突破"

    return True, None, None


# ============================================================
# 单标的回测
# ============================================================
def run_single(strategy_name, symbol, asset_type, capital, fee, start_ms, end_ms, tier="satellite"):
    """
    单标的回测（增强日志版）：
    - 显式打印每个周期的数据准备情况（DB 已有 / 增量 / 全量）
    - 回放过程按 10% 进度打印一次
    - 每笔开仓/平仓都打印详情
    """
    print(f"\n{'─'*70}")
    print(f"▶️  [{TIER_CN.get(tier, tier)}] {strategy_name} | {symbol}")

    slippage_rate = SLIPPAGE_BY_TIER.get(tier, 0.0005)

    # ---------- 数据准备 ----------
    print(f"  ▶️  【数据准备】")

    # 30m 直接拉
    klines_30m, info_30m = fetch_and_cache_klines(symbol, asset_type, "30m", BT_BARS_30M)
    if not klines_30m:
        print(f"    ❌ 30m 数据获取失败，跳过")
        return None
    print(f"    30m: 请求 {BT_BARS_30M} 根 → 实际 {len(klines_30m)} 根 | 源: {info_30m.get('actual', '?')}")

    # 4h 直接拉
    klines_4h, info_4h = fetch_and_cache_klines(symbol, asset_type, "4h", BT_BARS_4H)
    print(f"    4h:  请求 {BT_BARS_4H} 根 → 实际 {len(klines_4h) if klines_4h else 0} 根 | 源: {info_4h.get('actual', '?') if klines_4h else 'N/A'}")

    # 1h 从 30m 聚合
    klines_1h = aggregate_klines(klines_30m, 2)
    print(f"    1h:  从 30m 聚合 → {len(klines_1h)} 根")

    # 4h 兜底
    if not klines_4h:
        klines_4h = aggregate_klines(klines_30m, 8)
        print(f"    4h:  DB 拉取失败，从 30m 聚合 → {len(klines_4h)} 根")

    # 1d 直接拉
    klines_1d_direct, info_1d = fetch_and_cache_klines(symbol, asset_type, "1d", BT_BARS_1D)
    klines_1d = klines_1d_direct if klines_1d_direct else []
    if len(klines_1d) < 50:
        klines_1d_agg = aggregate_klines(klines_4h, 6)
        if len(klines_1d_agg) > len(klines_1d):
            klines_1d = klines_1d_agg
            print(f"    1d:  从 4h 聚合兜底 → {len(klines_1d)} 根")
        else:
            print(f"    1d:  请求 {BT_BARS_1D} 根 → 实际 {len(klines_1d)} 根 | 源: {info_1d.get('actual', '?') if info_1d else 'N/A'}")
    else:
        print(f"    1d:  请求 {BT_BARS_1D} 根 → 实际 {len(klines_1d)} 根 | 源: {info_1d.get('actual', '?') if info_1d else 'N/A'}")

    # 最低数据量检查
    if len(klines_30m) < 250:
        print(f"    ❌ 30m 数据不足（{len(klines_30m)} 根 < 250），跳过")
        return None

    data_mode = "futures" if info_30m["primary"] == "hyperliquid" else "spot"
    data_source = "Hyperliquid 合约" if data_mode == "futures" else "现货"
    print(f"    数据源: {data_source} | 滑点: {slippage_rate*100:.3f}% | 费率: {FUNDING_PER_8H*100:.3f}%/8h")

    # ---------- 时间戳索引 ----------
    ts_1h = [k["timestamp"] for k in klines_1h]
    ts_4h = [k["timestamp"] for k in klines_4h]
    ts_1d = [k["timestamp"] for k in klines_1d]
    ts_30m = [k["timestamp"] for k in klines_30m]

    # warmup 200 根用于算指标
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
        print(f"    ❌ 回放范围无效，跳过")
        return None

    backtest_start_ms = ts_30m[start_idx]
    backtest_end_ms = ts_30m[end_idx]
    total_bars = end_idx - start_idx + 1

    # ---------- 回放开始 ----------
    print(f"  ▶️  【回放开始】")
    print(f"    区间: {fmt_ts(backtest_start_ms)} ~ {fmt_ts(backtest_end_ms)}（{total_bars} 根 30m）")

    strategy = load_strategy(strategy_name)

    trades = []
    position = None
    pos_info = None
    current_cap_theoretical = capital
    current_cap_real = capital

    # 进度打印间隔（每 10% 打印一次）
    progress_step = max(1, total_bars // 10)
    next_progress = progress_step

    for i in range(start_idx, end_idx + 1):
        current_ts = ts_30m[i]
        window_30m = klines_30m[:i+1]

        # 多周期切片
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

        # ---------- 持仓管理 ----------
        if position is not None:
            still_hold, exit_price, reason = manage_position(pos_info, cp, ma10, atr)
            if not still_hold:
                entry = pos_info["entry"]
                direction = pos_info["direction"]
                pnl_pct = (exit_price - entry) / entry if direction.startswith("long") else (entry - exit_price) / entry

                risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
                position_value = min(current_cap_theoretical * 0.5, current_cap_theoretical * 0.02 / risk_pct) if risk_pct > 0 else current_cap_theoretical * 0.5

                # 理论盈亏（扣手续费）
                fee_cost = position_value * fee * 2
                pnl_theoretical = position_value * pnl_pct - fee_cost

                # 现实模拟：滑点
                slippage_cost = position_value * slippage_rate * 2

                # 现实模拟：资金费率（仅做空扣）
                holding_bars = i - pos_info["entry_idx"]
                holding_hours = holding_bars * 0.5
                settlements = holding_hours / 8
                if direction == "short":
                    funding_cost = position_value * FUNDING_PER_8H * settlements
                else:
                    funding_cost = 0.0

                pnl_real = pnl_theoretical - slippage_cost - funding_cost

                current_cap_theoretical += pnl_theoretical
                current_cap_real += pnl_real

                margin_10x = position_value / LEVERAGE
                coin_amount = position_value / entry if entry else 0

                # 🚀 平仓日志
                dir_cn = "多" if direction.startswith("long") else "空"
                pnl_emoji = "✅" if pnl_pct > 0 else "❌"
                print(f"      {pnl_emoji} 平仓#{len(trades)+1} [{dir_cn}] "
                      f"{fmt_ts_short(ts_30m[pos_info['entry_idx']])}→{fmt_ts_short(ts_30m[i])} | "
                      f"${entry:.4f}→${exit_price:.4f} | "
                      f"{pnl_pct*100:+.2f}% | {reason}")

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
                    "funding_usd": funding_cost,
                    "fee_usd": fee_cost,
                    "bars_held": holding_bars,
                    "exit_reason": reason,
                    "position_value": position_value,
                    "margin_10x": margin_10x,
                    "coin_amount": coin_amount,
                    "stop_price": pos_info["original_stop"],
                    "risk_pct": risk_pct * 100,
                })
                position = None
                pos_info = None

        # ---------- 开仓判断 ----------
        if position is None:
            try:
                res = strategy.evaluate(symbol, asset_type, md)
            except Exception as e:
                # 策略评估出错时打印，方便定位
                print(f"      ⚠️ 策略评估异常 @ bar {i}: {type(e).__name__}: {e}")
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
                    }
                    # 🚀 开仓日志
                    dir_cn = "多" if position.startswith("long") else "空"
                    reason_short = res.get("reason", "")[:30]
                    print(f"      🔫 开仓#{len(trades)+1} [{dir_cn}] "
                          f"{fmt_ts_short(ts_30m[i])} | "
                          f"入场${entry_price:.4f} 止损${stop_price:.4f} | {reason_short}")

        # 🚀 进度打印
        if (i - start_idx + 1) >= next_progress:
            pct = int((i - start_idx + 1) / total_bars * 100)
            hold_status = "是" if position is not None else "否"
            print(f"      进度 {pct}% ({i - start_idx + 1}根) | 已开仓 {len(trades)} 笔 | 持仓中: {hold_status}")
            next_progress += progress_step

    # ---------- 末尾强制平仓 ----------
    if position is not None and pos_info:
        cp = klines_30m[end_idx]["close"]
        entry = pos_info["entry"]
        direction = pos_info["direction"]
        pnl_pct = (cp - entry) / entry if direction.startswith("long") else (entry - cp) / entry
        risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
        position_value = min(current_cap_theoretical * 0.5, current_cap_theoretical * 0.02 / risk_pct) if risk_pct > 0 else current_cap_theoretical * 0.5

        fee_cost = position_value * fee * 2
        pnl_theoretical = position_value * pnl_pct - fee_cost
        slippage_cost = position_value * slippage_rate * 2
        holding_bars = end_idx - pos_info["entry_idx"]
        holding_hours = holding_bars * 0.5
        settlements = holding_hours / 8
        funding_cost = position_value * FUNDING_PER_8H * settlements if direction == "short" else 0.0
        pnl_real = pnl_theoretical - slippage_cost - funding_cost

        current_cap_theoretical += pnl_theoretical
        current_cap_real += pnl_real
        margin_10x = position_value / LEVERAGE
        coin_amount = position_value / entry if entry else 0

        dir_cn = "多" if direction.startswith("long") else "空"
        print(f"      🏁 末尾平仓 [{dir_cn}] "
              f"{fmt_ts_short(ts_30m[pos_info['entry_idx']])}→{fmt_ts_short(ts_30m[end_idx])} | "
              f"${entry:.4f}→${cp:.4f} | {pnl_pct*100:+.2f}%")

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
            "funding_usd": funding_cost,
            "fee_usd": fee_cost,
            "bars_held": holding_bars,
            "exit_reason": "末尾平仓",
            "position_value": position_value,
            "margin_10x": margin_10x,
            "coin_amount": coin_amount,
            "stop_price": pos_info["original_stop"],
            "risk_pct": risk_pct * 100,
        })

    # ---------- 统计 ----------
    print(f"  ▶️  【结算】")
    total = len(trades)
    buy_hold = (klines_30m[end_idx]["close"] - klines_30m[start_idx]["close"]) / klines_30m[start_idx]["close"] * 100

    if total == 0:
        print(f"    ⚠️  未触发任何交易")
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

    # 按方向统计
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
        if cap > peak: peak = cap
        dd = (peak - cap) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

    # 实盘资金曲线
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
    }

    rating, rating_desc = evaluate_strategy(report)
    report["rating"] = rating
    report["rating_desc"] = rating_desc

    print(f"    ✅ {total}笔 | 胜率{win_rate:.1f}% | "
          f"理论{total_return:.2f}% → 实盘{total_return_real:.2f}% | "
          f"基准{buy_hold:.2f}% | {rating}")
    return report


# ============================================================
# 从 config.json 读取回测标的（分层）
# ============================================================
def resolve_backtest_symbols(cfg, cli_symbols=None):
    """
    返回 (symbol_list, symbol_tier_map)
    - symbol_list: 全部回测标的
    - symbol_tier_map: {symbol: tier}
    """
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

    # 降级：从 backtest_watchlist 读
    bt_list = cfg.get("backtest_watchlist", [])
    if bt_list:
        return bt_list, {s: "satellite" for s in bt_list}

    # 再降级：backtest_defaults.symbols
    d = cfg.get("backtest_defaults", {})
    fallback = d.get("symbols", ["BTC_USDT"])
    return fallback, {s: "satellite" for s in fallback}


# ============================================================
# HTML 渲染 - 交易卡片
# ============================================================
def _render_trade_card(t, idx):
    """单笔交易卡片：关键信息加粗，次要信息缩小。"""
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
    html += "</div>"

    html += "<div style='margin-top:6px; font-size:12px; color:#888; line-height:1.6;'>"
    html += f"📦 仓位 <b style='color:#555;'>${pos_val:.0f}</b>（10x 保证金 <b style='color:#555;'>${margin:.0f}</b>，{coin_amt:.4f} 个）"
    html += f" &nbsp;·&nbsp; ⏱ 持仓 <b style='color:#555;'>{t['bars_held']}</b> 根"
    html += f" &nbsp;·&nbsp; 🚪 {t.get('exit_reason', '')}"
    html += "</div>"

    html += "<div style='margin-top:6px; font-size:12px; color:#666; line-height:1.6; background:#fff; padding:6px 10px; border-radius:4px; border-left:3px solid #95a5a6;'>"
    html += f"💵 <b>理论盈亏</b>（扣手续费）：<span style='color:{accent};font-weight:bold;'>{pnl_usd_str}</span>"
    html += f" &nbsp;|&nbsp; 滑点 <b style='color:#c0392b;'>-${slip:.2f}</b>"
    if fund > 0:
        html += f" &nbsp;|&nbsp; 资金费率 <b style='color:#c0392b;'>-${fund:.2f}</b>"
    html += f" &nbsp;|&nbsp; <b>实盘预估</b>：<span style='color:{accent};font-weight:bold;'>{pnl_real_str}</span>"
    html += "</div>"

    html += "</div>"
    return html


def _render_trades_cards(trades):
    if not trades:
        return ""
    sorted_trades = sorted(trades, key=lambda t: t.get("entry_time_ms", 0))
    html = "".join(_render_trade_card(t, i) for i, t in enumerate(sorted_trades, 1))
    html += (
        "<p style='color:#888; font-size:11px; margin:10px 0 0 0; line-height:1.6;'>"
        "💡 <b>时间</b>：北京时间（BJT, UTC+8）<br>"
        "💡 <b>理论盈亏</b>：扣开仓+平仓双边手续费<br>"
        "💡 <b>实盘预估</b>：再扣滑点 + 资金费率（仅做空）<br>"
        "💡 <b>离场原因</b>：<b>止损</b>=触及硬止损｜<b>MA10跌破/突破</b>=动态离场｜"
        "<b>末尾平仓</b>=区间结束时强制平仓"
        "</p>"
    )
    return html


def _render_consistency_notice(fee, capital):
    return f"""
<div style="background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); padding:18px; border-radius:10px; margin:20px 0; border-left:5px solid #1976d2; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
    <h3 style="margin:0 0 12px 0; color:#0d47a1; font-size:17px;">⚖️ 回测口径说明</h3>
    <table style="width:100%; font-size:13px; border-collapse:collapse;">
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; width:150px; color:#0d47a1; font-weight:bold;">手续费</td>
            <td style="padding:6px 8px;">✅ 双边 <b>{fee*200:.3f}%</b>（开仓+平仓各 {fee*100:.3f}%）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">滑点</td>
            <td style="padding:6px 8px;">✅ 分层：核心 <b>0.02%</b>｜卫星 <b>0.05%</b>｜观察 <b>0.10%</b>（双边）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">资金费率</td>
            <td style="padding:6px 8px;">✅ 做空持仓成本 <b>0.01% / 8h</b>；做多不计（保守估计）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">硬止损</td>
            <td style="padding:6px 8px;">✅ 按币种分层 ATR 倍数（1.5× / 2.0× / 2.5×）+ 1.5%~3.5% 双保险</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">移动止盈</td>
            <td style="padding:6px 8px;">✅ 1×ATR 保本 → 2×ATR 上移 → 3×ATR 继续上移</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">MA10 离场</td>
            <td style="padding:6px 8px;">✅ 价格跌破/突破 MA10 立即离场</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">轨道3（独立）</td>
            <td style="padding:6px 8px;">✅ 暴跌反弹不受状态机约束，但需 4/4 全满足（回撤≥20% + RSI≤25 + KDJ<-15 + 止跌形态）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">波动率熔断</td>
            <td style="padding:6px 8px;">✅ ATR/价格 > 3% 时所有轨道自动暂停</td>
        </tr>
        <tr>
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">仓位基数</td>
            <td style="padding:6px 8px;">⚠️ 用当前滚动资金（复利），实盘按固定 {capital}U 反推 → 收益会被复利放大</td>
        </tr>
    </table>
    <p style="margin:12px 0 0 0; padding:8px; background:#fff3cd; border-radius:6px; font-size:13px; color:#856404;">
    🎯 <b>看什么</b>：请优先看「<b>实盘预估收益</b>」列 —— 这是扣完手续费、滑点、资金费率后的现实收益。
    </p>
</div>
"""


def _render_single_detail(r, fee=0.0005, capital=10000):
    """
    单标的汇总卡（精简版，不含交易明细列表）。
    交易明细已移到 CSV 附件中。
    """
    tier = r.get("tier", "satellite")
    tier_cn = TIER_CN.get(tier, tier)
    tier_icon = TIER_ICON.get(tier, "•")
    tier_color = TIER_COLOR.get(tier, "#333")

    html = "<div style='background:#fff;padding:18px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,0.08);margin-bottom:15px;'>"

    # 标题行
    html += "<div style='display:flex; justify-content:space-between; align-items:center; border-bottom:2px dashed #eee; padding-bottom:10px; margin-bottom:12px;'>"
    html += f"<div>"
    html += f"<span style='background:{tier_color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold;margin-right:8px;'>{tier_icon} {tier_cn}</span>"
    html += f"<span style='font-size:18px; font-weight:900; color:#2c3e50;'>{r['symbol'].replace('_USDT','')}</span>"
    html += f"<span style='margin-left:10px; font-size:14px; color:#e67e22; font-weight:bold;'>{r['rating']}</span>"
    html += f"</div>"
    html += f"<div style='font-size:12px; color:#888;'>{r['data_source']}</div>"
    html += "</div>"

    bs = r.get("backtest_start_ms")
    be = r.get("backtest_end_ms")
    if bs and be:
        html += (f"<p style='color:#888;font-size:12px;margin:0 0 10px 0;'>"
                 f"📅 {fmt_ts(bs)} ~ {fmt_ts(be)}（{r['bars_total']} 根 30m）</p>")

    if r["total_trades"] == 0:
        html += f"<p style='color:#999;padding:12px;background:#f8f9fa;border-radius:6px;font-size:13px;'>⚠️ 未触发交易。基准收益：<b>{r['benchmark_return']}%</b></p>"
    else:
        ret_color = "#27ae60" if r["total_return"] > 0 else "#c0392b"
        rret_color = "#27ae60" if r["total_return_real"] > 0 else "#c0392b"
        exc_color = "#27ae60" if r["excess_return_real"] > 0 else "#c0392b"

        html += "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(100px, 1fr)); gap:8px; margin-bottom:10px;'>"

        def metric_box(label, value, color="#2c3e50", is_important=False):
            border = "2px solid #e74c3c" if is_important else "1px solid #eee"
            return (
                f"<div style='background:#fafbfc; padding:8px; border-radius:6px; text-align:center; border:{border};'>"
                f"<div style='color:#888; font-size:10.5px; margin-bottom:2px;'>{label}</div>"
                f"<div style='color:{color}; font-size:15px; font-weight:bold;'>{value}</div>"
                "</div>"
            )

        html += metric_box("交易次数", r["total_trades"])
        html += metric_box("胜率", f"{r['win_rate']}%")
        html += metric_box("理论收益", f"{r['total_return']}%", ret_color)
        html += metric_box("实盘预估", f"{r['total_return_real']}%", rret_color, is_important=True)
        html += metric_box("实盘超额", f"{r['excess_return_real']}%", exc_color)
        html += metric_box("回撤", f"{r['max_dd']}%", "#e67e22")
        html += metric_box("盈亏比", r["profit_factor"])
        html += metric_box("夏普", r["sharpe"])
        html += "</div>"

        html += "<div style='background:#f8f9fa; padding:8px 12px; border-radius:6px; font-size:12px;'>"
        html += f"🟢 做多 <b>{r['long_trades']}</b> 笔（{r['long_win_rate']}%） &nbsp;|&nbsp; "
        html += f"🔴 做空 <b>{r['short_trades']}</b> 笔（{r['short_win_rate']}%） &nbsp;|&nbsp; "
        html += f"⏱ 平均持仓 <b>{r['avg_bars_held']}</b> 根"
        html += "</div>"

    html += "</div>"
    return html


def _render_tier_table(reports, tier):
    """渲染某一层的汇总表"""
    if not reports:
        return ""
    tier_icon = TIER_ICON.get(tier, "•")
    tier_cn = TIER_CN.get(tier, tier)
    tier_color = TIER_COLOR.get(tier, "#333")

    # 按实盘预估收益排序
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
    html += "<p style='color:#888;font-size:11px;margin-top:6px;'>💡 按实盘预估收益降序排列。「实盘预估」已扣除滑点和资金费率。</p>"
    return html
    
def _generate_trades_csv(all_reports):
    """
    生成合并的交易明细 CSV（所有标的合并到一个文件）。
    加 UTF-8 BOM，确保 Excel 打开不乱码。
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "标的", "分层", "方向", "入场时间", "入场价", "出场时间", "出场价",
        "盈亏%", "理论盈亏U", "实盘盈亏U", "滑点U", "资金费率U", "手续费U",
        "持仓根数", "离场原因", "硬止损价", "风险%"
    ])

    # 按分层 + 实盘收益排序，方便 Excel 里阅读
    tier_order = {"core": 0, "satellite": 1, "watch": 2}
    sorted_reports = sorted(all_reports,
                            key=lambda x: (tier_order.get(x.get("tier", "satellite"), 1),
                                           -x.get("total_return_real", 0)))

    for r in sorted_reports:
        sym = r["symbol"].replace("_USDT", "")
        tier = r.get("tier", "satellite")
        for t in r.get("trades", []):
            writer.writerow([
                sym,
                tier,
                "多" if t["direction"].startswith("long") else "空",
                fmt_ts(t.get("entry_time_ms")),
                round(t["entry_price"], 6),
                fmt_ts(t.get("exit_time_ms")),
                round(t["exit_price"], 6),
                round(t["pnl_pct"] * 100, 2),
                round(t.get("pnl_usd", 0), 2),
                round(t.get("pnl_usd_real", 0), 2),
                round(t.get("slippage_usd", 0), 2),
                round(t.get("funding_usd", 0), 2),
                round(t.get("fee_usd", 0), 2),
                t.get("bars_held", 0),
                t.get("exit_reason", ""),
                round(t.get("stop_price", 0), 6),
                round(t.get("risk_pct", 0), 2),
            ])

    # UTF-8 BOM 让 Excel 正确识别编码
    return "\ufeff" + buf.getvalue()

def build_tiered_backtest_html(all_reports, strategies, period_desc, fee=0.0005, capital=10000):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")

    # 分层
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

    # ---------- 全局总览 ----------
    html += "<h3 style='margin-top:30px;'>📈 全局总览</h3>"
    if with_trades:
        avg_ret = sum(r["total_return"] for r in with_trades) / len(with_trades)
        avg_ret_real = sum(r["total_return_real"] for r in with_trades) / len(with_trades)
        avg_win = sum(r["win_rate"] for r in with_trades) / len(with_trades)
        total_trades = sum(r["total_trades"] for r in with_trades)
    else:
        avg_ret = avg_ret_real = avg_win = total_trades = 0

    html += "<div style='display:flex;flex-wrap:wrap;gap:12px;margin:15px 0;'>"
    cards = [
        ("测试标的", total, "#2c3e50"),
        ("核心/卫星/观察", f"{len(by_tier['core'])}/{len(by_tier['satellite'])}/{len(by_tier['watch'])}", "#3498db"),
        ("有交易", len(with_trades), "#27ae60"),
        ("总交易笔数", total_trades, "#3498db"),
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

    # ---------- 分层汇总表 ----------
    for tier in TIER_ORDER:
        if by_tier[tier]:
            html += _render_tier_table(by_tier[tier], tier)

    # ---------- 分层详细汇总卡（不含交易明细，明细在附件 CSV 里） ----------
    for tier in TIER_ORDER:
        reports_in_tier = sorted(
            [r for r in by_tier[tier]],
            key=lambda x: x.get("total_return_real", 0), reverse=True
        )
        if not reports_in_tier:
            continue
        tier_icon = TIER_ICON.get(tier, "•")
        tier_cn = TIER_CN.get(tier, tier)
        tier_color = TIER_COLOR.get(tier, "#333")

        html += f"<h3 style='margin-top:40px; color:{tier_color}; border-left:5px solid {tier_color}; padding-left:10px;'>"
        html += f"🔍 {tier_icon} {tier_cn} · 各标的汇总（{len(reports_in_tier)} 个）</h3>"
        # 所有标的都展示，只显示汇总卡，不显示明细列表
        for r in reports_in_tier:
            html += _render_single_detail(r, fee=fee, capital=capital)

    # ---------- 附件提示 ----------
    html += """
    <div style="background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%); padding:20px; border-radius:10px; margin-top:30px; border-left:5px solid #f39c12;">
        <h3 style="margin:0 0 10px 0; color:#b8860b; font-size:17px;">📎 完整交易明细</h3>
        <p style="margin:0; font-size:14px; color:#555; line-height:1.7;">
            为避免邮件过长，<b>每笔交易的详细数据已导出为 CSV 附件</b>。<br>
            文件名格式：<code>backtest_trades_YYYYMMDD_HHMM.csv</code><br>
            用 Excel 打开即可筛选、排序、透视分析。
        </p>
        <p style="margin:10px 0 0 0; font-size:13px; color:#888;">
            CSV 字段：标的、分层、方向、入场/出场时间、入场/出场价、盈亏%、理论盈亏、实盘盈亏、滑点、资金费率、手续费、持仓根数、离场原因、硬止损、风险%
        </p>
    </div>
    """

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

    print(f"\n{'='*70}")
    print(f"📊 参谋长分层回测引擎")
    print(f"   策略：{strategies}")
    print(f"   标的数：{len(symbol_list)}")
    tier_count = {"core": 0, "satellite": 0, "watch": 0}
    for s in symbol_list:
        tier_count[tier_map.get(s, "satellite")] = tier_count.get(tier_map.get(s, "satellite"), 0) + 1
    print(f"   分层：核心 {tier_count.get('core',0)} / 卫星 {tier_count.get('satellite',0)} / 观察 {tier_count.get('watch',0)}")
    print(f"   区间：{period_desc}")
    print(f"   本金：{capital}U | 手续费：{fee*100}%")
    print(f"   数据窗口：30m={BT_BARS_30M}根 / 4h={BT_BARS_4H}根 / 1d={BT_BARS_1D}根")
    print(f"{'='*70}\n")

    all_reports = []
    t0 = time.time()
    for strat in strategies:
        for i, sym in enumerate(symbol_list, 1):
            tier = tier_map.get(sym, "satellite")
            print(f"\n[{i}/{len(symbol_list)}] {strat} | {sym} [{tier}] | 已用时 {int(time.time()-t0)}s")
            rep = run_single(strat, sym, asset_type, capital, fee, start_ms, end_ms, tier=tier)
            if rep:
                all_reports.append(rep)
            time.sleep(0.3)

    if not all_reports:
        print("\n❌ 无有效回测结果")
        sys.exit(1)

    print(f"\n{'='*70}")
    print(f"✅ 回测完成，总用时 {int(time.time()-t0)}s")
    print(f"   有效报告：{len(all_reports)} 份")
    with_trades = sum(1 for r in all_reports if r["total_trades"] > 0)
    print(f"   有交易：{with_trades} 份")
    print(f"{'='*70}")

    html = build_tiered_backtest_html(all_reports, strategies, period_desc, fee=fee, capital=capital)
    now_str = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【参谋长分层回测】{now_str} | {len(all_reports)}标的 | {with_trades}个有交易"

    # 🚀 生成合并的 CSV 附件
    try:
        csv_content = _generate_trades_csv(all_reports)
        csv_filename = f"backtest_trades_{datetime.now(BJT).strftime('%Y%m%d_%H%M')}.csv"
        attachments = [(csv_filename, csv_content.encode("utf-8"), "text/csv")]
        total_trades = sum(len(r.get("trades", [])) for r in all_reports)
        print(f"\n📎 已生成 CSV 附件：{csv_filename}（{total_trades} 笔交易）")
    except Exception as e:
        print(f"\n⚠️ CSV 生成失败（将只发 HTML）：{e}")
        attachments = None

    try:
        send_html_email(subject, html, attachments=attachments)
        print("\n📧 回测报告已发送")
    except Exception as e:
        print(f"\n❌ 邮件发送失败：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
