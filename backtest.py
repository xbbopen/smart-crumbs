# -*- coding: utf-8 -*-
"""
回测引擎 v5 - 卡片式报告 + 一致性说明

v5 改动：
1. 每笔交易记录仓位信息（position_value / margin_10x / coin_amount）
2. 交易明细改为卡片式排版，关键信息加粗、次要信息缩小
3. 新增"回测与实盘一致性说明"章节
4. 详细说明每笔交易时用的止损/止盈逻辑
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

BJT = timezone(timedelta(hours=8))
LEVERAGE = 10      # 回测统一按 10 倍杠杆口径计算保证金
CAPITAL = 10000    # 报告里展示的"标准本金"，用于换算保证金


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


def manage_position(pos, cp, ma10, atr):
    """
    持仓管理：
    - 硬止损（按币种分层的 ATR 倍数）
    - 移动止盈阶梯：1×ATR 保本 → 2×ATR → 3×ATR
    - MA10 动态离场
    返回 (still_hold, exit_price, exit_reason)
    """
    direction = pos["direction"]
    entry = pos["entry"]
    stop = pos["stop"]
    tp_stage = pos["tp_stage"]

    if direction.startswith("long"):
        if cp <= stop:
            return False, stop, "止损"
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
        if ma10 and cp < ma10:
            return False, cp, "MA10跌破"
    else:
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


def run_single(strategy_name, symbol, asset_type, capital, fee, start_ms, end_ms):
    print(f"\n▶️  回测 {strategy_name} | {symbol}")

    klines_30m, info_30m = fetch_and_cache_klines(symbol, asset_type, "30m", 500)
    if not klines_30m:
        print(f"⚠️  {symbol} 30m 数据获取失败")
        return None
    klines_4h, info_4h = fetch_and_cache_klines(symbol, asset_type, "4h", 2000)

    klines_1h = aggregate_klines(klines_30m, 2)
    if not klines_4h:
        klines_4h = aggregate_klines(klines_30m, 8)

    klines_1d_direct, _ = fetch_and_cache_klines(symbol, asset_type, "1d", 400)
    klines_1d = klines_1d_direct if klines_1d_direct else []
    if len(klines_1d) < 50:
        klines_1d_agg = aggregate_klines(klines_4h, 6)
        if len(klines_1d_agg) > len(klines_1d):
            klines_1d = klines_1d_agg

    if len(klines_30m) < 250:
        print(f"⚠️  {symbol} 30m 数据不足（{len(klines_30m)}根），至少需要250根")
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
        print(f"⚠️  {symbol} 回放范围无效")
        return None

    backtest_start_ms = ts_30m[start_idx]
    backtest_end_ms = ts_30m[end_idx]
    print(f"   数据源：{info_30m['actual']} | 30m:{len(klines_30m)}根 | 4h:{len(klines_4h)}根 | 1d:{len(klines_1d)}根")
    print(f"   回放区间：{fmt_ts(backtest_start_ms)} ~ {fmt_ts(backtest_end_ms)}")

    strategy = load_strategy(strategy_name)
    data_mode = "futures" if info_30m["primary"] == "hyperliquid" else "spot"
    data_source = "Hyperliquid 合约" if data_mode == "futures" else "现货"

    trades = []
    position = None
    pos_info = None
    current_cap = capital

    for i in range(start_idx, end_idx + 1):
        current_ts = ts_30m[i]
        window_30m = klines_30m[:i+1]

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

        if position is not None:
            still_hold, exit_price, reason = manage_position(pos_info, cp, ma10, atr)
            if not still_hold:
                entry = pos_info["entry"]
                direction = pos_info["direction"]
                pnl_pct = (exit_price - entry) / entry if direction.startswith("long") else (entry - exit_price) / entry

                risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
                position_value = min(current_cap * 0.5, current_cap * 0.02 / risk_pct) if risk_pct > 0 else current_cap * 0.5
                pnl = position_value * pnl_pct - position_value * fee * 2
                current_cap += pnl

                # 🚀 新增：记录仓位信息
                margin_10x = position_value / LEVERAGE
                coin_amount = position_value / entry if entry else 0

                trades.append({
                    "entry_idx": pos_info["entry_idx"], "exit_idx": i,
                    "direction": direction,
                    "entry_price": entry, "exit_price": exit_price,
                    "entry_time_ms": ts_30m[pos_info["entry_idx"]],
                    "exit_time_ms": ts_30m[i],
                    "pnl_pct": pnl_pct, "pnl_usd": pnl,
                    "bars_held": i - pos_info["entry_idx"],
                    "exit_reason": reason,
                    "position_value": position_value,
                    "margin_10x": margin_10x,
                    "coin_amount": coin_amount,
                    "stop_price": pos_info["original_stop"],
                    "risk_pct": risk_pct * 100,
                })
                position = None
                pos_info = None

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
                    }

    if position is not None and pos_info:
        cp = klines_30m[end_idx]["close"]
        entry = pos_info["entry"]
        direction = pos_info["direction"]
        pnl_pct = (cp - entry) / entry if direction.startswith("long") else (entry - cp) / entry
        risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
        position_value = min(current_cap * 0.5, current_cap * 0.02 / risk_pct) if risk_pct > 0 else current_cap * 0.5
        pnl = position_value * pnl_pct - position_value * fee * 2
        current_cap += pnl
        margin_10x = position_value / LEVERAGE
        coin_amount = position_value / entry if entry else 0
        trades.append({
            "entry_idx": pos_info["entry_idx"], "exit_idx": end_idx,
            "direction": direction,
            "entry_price": entry, "exit_price": cp,
            "entry_time_ms": ts_30m[pos_info["entry_idx"]],
            "exit_time_ms": ts_30m[end_idx],
            "pnl_pct": pnl_pct, "pnl_usd": pnl,
            "bars_held": end_idx - pos_info["entry_idx"],
            "exit_reason": "末尾平仓",
            "position_value": position_value,
            "margin_10x": margin_10x,
            "coin_amount": coin_amount,
            "stop_price": pos_info["original_stop"],
            "risk_pct": risk_pct * 100,
        })

    total = len(trades)
    buy_hold = (klines_30m[end_idx]["close"] - klines_30m[start_idx]["close"]) / klines_30m[start_idx]["close"] * 100

    if total == 0:
        print(f"   ⚠️  未触发任何交易")
        return {
            "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
            "data_mode": data_mode, "data_source": data_source,
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "final_capital": capital, "total_return": 0, "max_dd": 0,
            "benchmark_return": round(buy_hold, 2),
            "excess_return": round(-buy_hold, 2),
            "sharpe": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "avg_bars_held": 0, "rating": "⚠️ 无交易",
            "rating_desc": "策略在回测区间未触发任何信号",
            "long_trades": 0, "short_trades": 0,
            "long_win_rate": 0, "short_win_rate": 0,
            "bars_total": end_idx - start_idx + 1,
            "backtest_start_ms": backtest_start_ms,
            "backtest_end_ms": backtest_end_ms,
            "trades": [],
        }

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / total * 100
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t["pnl_pct"] for t in wins) / sum(t["pnl_pct"] for t in losses)) if losses else 999
    avg_bars_held = sum(t["bars_held"] for t in trades) / total
    returns_seq = [t["pnl_pct"] for t in trades]
    sharpe = calc_sharpe(returns_seq)

    long_trades = [t for t in trades if t["direction"].startswith("long")]
    short_trades = [t for t in trades if t["direction"] == "short"]
    long_win_rate = len([t for t in long_trades if t["pnl_pct"] > 0]) / len(long_trades) * 100 if long_trades else 0
    short_win_rate = len([t for t in short_trades if t["pnl_pct"] > 0]) / len(short_trades) * 100 if short_trades else 0

    cap = capital
    peak = cap
    max_dd = 0
    for t in trades:
        cap += t["pnl_usd"]
        if cap > peak: peak = cap
        dd = (peak - cap) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

    total_return = (current_cap - capital) / capital * 100

    report = {
        "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
        "data_mode": data_mode, "data_source": data_source,
        "total_trades": total, "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "final_capital": round(current_cap, 2),
        "total_return": round(total_return, 2),
        "max_dd": round(max_dd * 100, 2),
        "benchmark_return": round(buy_hold, 2),
        "excess_return": round(total_return - buy_hold, 2),
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
        "trades": trades,
    }

    rating, rating_desc = evaluate_strategy(report)
    report["rating"] = rating
    report["rating_desc"] = rating_desc

    print(f"   ✅ {total}笔 | 胜率{win_rate:.1f}% | 收益{total_return:.2f}% | 基准{buy_hold:.2f}% | 超额{total_return-buy_hold:.2f}% | 夏普{sharpe:.2f} | {rating}")
    return report


# ============================================================
# 全市场扫描：标的过滤
# ============================================================
def filter_symbols_by_age(symbols, asset_type, min_days=90):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    min_ts = now_ms - min_days * 24 * 3600 * 1000

    valid = []
    print(f"\n{'='*70}")
    print(f"🔍 检查上线时间（要求 ≥ {min_days} 天），共 {len(symbols)} 个候选标的")
    print(f"{'='*70}")

    for idx, sym in enumerate(symbols, 1):
        try:
            klines_1d, _ = fetch_and_cache_klines(sym, asset_type, "1d", 400)
            if not klines_1d:
                print(f"[{idx}/{len(symbols)}] {sym}: 无 1D 数据 ❌")
                continue
            first_ts = klines_1d[0]["timestamp"]
            age_days = (now_ms - first_ts) / 1000 / 86400
            if first_ts <= min_ts:
                valid.append(sym)
                print(f"[{idx}/{len(symbols)}] {sym}: 上线 {age_days:.0f} 天 ✅")
            else:
                print(f"[{idx}/{len(symbols)}] {sym}: 上线仅 {age_days:.0f} 天 ❌")
        except Exception as e:
            print(f"[{idx}/{len(symbols)}] {sym}: 检查失败 {type(e).__name__} ❌")
        time.sleep(0.3)

    print(f"\n✅ 通过检查：{len(valid)}/{len(symbols)}")
    return valid


# ============================================================
# 🚀 交易卡片渲染（v5 核心改动）
# ============================================================
def _render_trade_card(t, idx):
    """单笔交易卡片：关键信息加粗，次要信息缩小。"""
    is_win = t["pnl_pct"] > 0
    is_long = t["direction"].startswith("long")

    # 色系
    accent = "#27ae60" if is_win else "#c0392b"
    bg = "#f0faf3" if is_win else "#fdf0f0"
    dir_color = "#27ae60" if is_long else "#c0392b"
    dir_label = "做多" if is_long else "做空"
    dir_arrow = "⬆" if is_long else "⬇"

    pnl_pct = t["pnl_pct"] * 100
    pnl_pct_str = f"+{pnl_pct:.2f}%" if is_win else f"{pnl_pct:.2f}%"
    pnl_usd = t["pnl_usd"]
    pnl_usd_str = f"+${pnl_usd:.2f}" if pnl_usd > 0 else f"-${abs(pnl_usd):.2f}"

    entry_time = fmt_ts_short(t.get("entry_time_ms"))
    exit_time = fmt_ts_short(t.get("exit_time_ms"))
    pos_val = t.get("position_value", 0)
    margin = t.get("margin_10x", 0)
    coin_amt = t.get("coin_amount", 0)

    html = (
        f"<div style='border-left:5px solid {accent}; background:{bg}; "
        f"padding:12px 15px; margin-bottom:10px; border-radius:0 6px 6px 0;'>"
    )

    # 第一行：序号 + 方向 + 时间 + 盈亏%（大号焦点）
    html += "<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
    html += "<div style='display:flex; align-items:center; gap:10px;'>"
    html += f"<span style='background:#2c3e50; color:#fff; padding:2px 10px; border-radius:4px; font-weight:bold; font-size:13px;'>#{idx}</span>"
    html += f"<span style='background:{dir_color}; color:#fff; padding:2px 10px; border-radius:4px; font-size:12px; font-weight:bold;'>{dir_arrow} {dir_label}</span>"
    html += f"<span style='color:#666; font-size:13px;'>{entry_time} → {exit_time}</span>"
    html += "</div>"
    html += f"<div style='font-size:20px; font-weight:900; color:{accent}; letter-spacing:0.5px;'>{pnl_pct_str}</div>"
    html += "</div>"

    # 第二行：价格链路
    html += "<div style='margin-top:8px; font-size:13px; color:#333;'>"
    html += f"<span style='color:#888;'>入场</span> <b>${t['entry_price']:.4f}</b>"
    html += f" <span style='color:#888;'>→</span> "
    html += f"<span style='color:#888;'>出场</span> <b>${t['exit_price']:.4f}</b>"
    html += f" <span style='color:#888;'>|</span> "
    html += f"<span style='color:#888;'>盈亏</span> <b style='color:{accent};'>{pnl_usd_str}</b>"
    html += "</div>"

    # 第三行：仓位 + 持仓 + 离场原因（次要信息，小号灰色）
    html += "<div style='margin-top:6px; font-size:12px; color:#888; line-height:1.6;'>"
    html += f"📦 仓位 <b style='color:#555;'>${pos_val:.0f}</b>（10x 保证金 <b style='color:#555;'>${margin:.0f}</b>，{coin_amt:.4f} 个）"
    html += f" &nbsp;·&nbsp; ⏱ 持仓 <b style='color:#555;'>{t['bars_held']}</b> 根"
    html += f" &nbsp;·&nbsp; 🚪 {t.get('exit_reason', '')}"
    html += "</div>"

    html += "</div>"
    return html


def _render_trades_cards(trades, max_rows=None):
    """渲染交易卡片列表。"""
    if not trades:
        return ""
    # 时间正序
    sorted_trades = sorted(trades, key=lambda t: t.get("entry_time_ms", 0))
    display = sorted_trades if max_rows is None else sorted_trades[-max_rows:]

    html = "".join(_render_trade_card(t, i) for i, t in enumerate(display, 1))
    html += (
        "<p style='color:#888; font-size:11px; margin:10px 0 0 0; line-height:1.6;'>"
        "💡 <b>时间</b>：北京时间（BJT, UTC+8）｜入场时间为信号触发的 30m K 线开盘时间，"
        "可直接对照交易所 K 线核对<br>"
        "💡 <b>仓位</b>：2% 风险规则反推的仓位价值，10 倍杠杆下的保证金按仓位/10 计算<br>"
        "💡 <b>离场</b>：<b>止损</b>=触及硬止损价｜<b>MA10跌破/突破</b>=动态离场线触发｜"
        "<b>末尾平仓</b>=回测区间结束时的强制平仓"
        "</p>"
    )
    return html


# ============================================================
# 一致性说明
# ============================================================
def _render_consistency_notice(fee, capital):
    return f"""
<div style="background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); padding:18px; border-radius:10px; margin:20px 0; border-left:5px solid #1976d2; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
    <h3 style="margin:0 0 12px 0; color:#0d47a1; font-size:17px;">⚖️ 回测与实盘一致性说明</h3>
    <table style="width:100%; font-size:13px; border-collapse:collapse;">
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; width:130px; color:#0d47a1; font-weight:bold;">硬止损</td>
            <td style="padding:6px 8px;">✅ <b>完全一致</b>。按币种分层的 ATR 倍数：主流币 1.5×，中市值 2.0×，altcoin 2.5×</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">移动止盈阶梯</td>
            <td style="padding:6px 8px;">✅ <b>完全一致</b>。1×ATR 保本 → 2×ATR 上移 → 3×ATR 继续上移</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">MA10 动态离场</td>
            <td style="padding:6px 8px;">✅ <b>完全一致</b>。价格跌破/突破 MA10 立即离场</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">杠杆口径</td>
            <td style="padding:6px 8px;">✅ <b>按 10 倍杠杆</b>计算保证金占用（不影响盈亏，只影响展示）</td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">仓位大小</td>
            <td style="padding:6px 8px;">⚠️ <b>算法一致，基数不同</b>。实盘按固定 {capital}U 本金反推，回测用当前滚动资金（复利）。<br>
            <span style="color:#666;">→ 回测的收益会被复利放大，实际对比时可关注"胜率"和"盈亏比"而非绝对收益率</span></td>
        </tr>
        <tr style="border-bottom:1px solid #90caf9;">
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">手续费</td>
            <td style="padding:6px 8px;">⚠️ 回测按 <b>{fee*100:.3f}%</b> 双边收取。实盘还需加实际成交滑点，通常多 0.02%-0.05%</td>
        </tr>
        <tr>
            <td style="padding:6px 8px; color:#0d47a1; font-weight:bold;">止盈 50% 减仓</td>
            <td style="padding:6px 8px;">⚠️ <b>回测未实现</b>。实盘报告里提到"3×ATR 止盈 50% 仓位"，回测里只把止损上移，让剩余仓位继续跑<br>
            <span style="color:#666;">→ 回测的持仓时间比实盘更长，收益弹性也更大</span></td>
        </tr>
    </table>
</div>
"""


# ============================================================
# 单标的详细卡片
# ============================================================
def _render_single_detail(r, fee=0.0005, capital=10000):
    html = "<div style='background:#fff;padding:20px;border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,0.08);margin-bottom:20px;'>"

    # 标题行
    html += "<div style='display:flex; justify-content:space-between; align-items:center; border-bottom:2px dashed #eee; padding-bottom:12px; margin-bottom:15px;'>"
    html += f"<div><span style='font-size:22px; font-weight:900; color:#2c3e50;'>💥 {r['symbol'].replace('_USDT','')}</span>"
    html += f"<span style='margin-left:12px; font-size:15px; color:#e67e22; font-weight:bold;'>{r['rating']}</span></div>"
    html += f"<div style='font-size:13px; color:#888;'>{r['data_source']}</div>"
    html += "</div>"

    # 数据区间
    bs = r.get("backtest_start_ms")
    be = r.get("backtest_end_ms")
    if bs and be:
        html += (f"<p style='color:#555;font-size:13px;margin:0 0 12px 0;'>"
                 f"<b>📅 回测区间：</b>{fmt_ts(bs)} ~ {fmt_ts(be)}"
                 f"（共 {r['bars_total']} 根 30m K线）</p>")

    html += f"<p style='color:#666;font-size:13px;margin:0 0 15px 0;'>{r['rating_desc']}</p>"

    if r["total_trades"] == 0:
        html += f"<p style='color:#999;padding:15px;background:#f8f9fa;border-radius:6px;'>⚠️ 本区间未触发任何交易信号。基准收益：<b>{r['benchmark_return']}%</b></p>"
    else:
        # 核心指标卡片组
        ret_color = "#27ae60" if r["total_return"] > 0 else "#c0392b"
        exc_color = "#27ae60" if r["excess_return"] > 0 else "#c0392b"

        html += "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(110px, 1fr)); gap:10px; margin-bottom:15px;'>"

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
        html += metric_box("策略收益", f"{r['total_return']}%", ret_color, is_important=True)
        html += metric_box("基准收益", f"{r['benchmark_return']}%", "#666")
        html += metric_box("超额收益", f"{r['excess_return']}%", exc_color, is_important=True)
        html += metric_box("最大回撤", f"{r['max_dd']}%", "#e67e22")
        html += metric_box("盈亏比", r["profit_factor"])
        html += metric_box("夏普比率", r["sharpe"])
        html += "</div>"

        # 多空分布
        html += "<div style='background:#f8f9fa; padding:12px; border-radius:8px; font-size:13px; margin-bottom:15px;'>"
        html += f"<span style='color:#27ae60;'>🟢 做多：<b>{r['long_trades']}</b> 笔（胜率 {r['long_win_rate']}%）</span>"
        html += " &nbsp;&nbsp;|&nbsp;&nbsp; "
        html += f"<span style='color:#c0392b;'>🔴 做空：<b>{r['short_trades']}</b> 笔（胜率 {r['short_win_rate']}%）</span>"
        html += f" &nbsp;&nbsp;|&nbsp;&nbsp; <span style='color:#666;'>平均持仓 <b>{r['avg_bars_held']}</b> 根30m</span>"
        html += "</div>"

        # 交易明细卡片
        html += "<h4 style='margin:20px 0 12px 0; color:#2c3e50; font-size:15px;'>📝 交易明细（共 {} 笔）</h4>".format(r["total_trades"])
        html += _render_trades_cards(r["trades"])

    html += "</div>"
    return html


# ============================================================
# 全市场回测报告
# ============================================================
def build_universe_backtest_html(reports, strategies, period_desc, fee=0.0005, capital=10000):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")

    total = len(reports)
    with_trades = [r for r in reports if r["total_trades"] > 0]
    no_trades = [r for r in reports if r["total_trades"] == 0]

    if with_trades:
        avg_ret = sum(r["total_return"] for r in with_trades) / len(with_trades)
        avg_exc = sum(r["excess_return"] for r in with_trades) / len(with_trades)
        avg_win = sum(r["win_rate"] for r in with_trades) / len(with_trades)
        total_trades = sum(r["total_trades"] for r in with_trades)
        total_long = sum(r["long_trades"] for r in with_trades)
        total_short = sum(r["short_trades"] for r in with_trades)
        long_wins = sum(1 for r in with_trades for t in r["trades"] if t["direction"].startswith("long") and t["pnl_pct"] > 0)
        short_wins = sum(1 for r in with_trades for t in r["trades"] if t["direction"] == "short" and t["pnl_pct"] > 0)
        long_wr = long_wins / total_long * 100 if total_long else 0
        short_wr = short_wins / total_short * 100 if total_short else 0
    else:
        avg_ret = avg_exc = avg_win = total_trades = total_long = total_short = 0
        long_wr = short_wr = 0

    rating_counts = {}
    for r in with_trades:
        rating_counts[r["rating"]] = rating_counts.get(r["rating"], 0) + 1

    sorted_by_ret = sorted(with_trades, key=lambda x: x["total_return"], reverse=True)
    top_n = sorted_by_ret[:10]
    bottom_n = sorted_by_ret[-10:] if len(sorted_by_ret) > 10 else []
    detail_n = min(20, len(sorted_by_ret))
    detail_list = sorted_by_ret[:detail_n]

    html = "<html><body style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Arial,sans-serif;max-width:1200px;margin:auto;padding:20px;background:#f4f6f8;color:#2c3e50;'>"
    html += "<h2 style='border-bottom:3px solid #e74c3c;padding-bottom:12px;'>📊 参谋长全市场回测报告</h2>"
    html += f"<p style='color:#666;'><b>生成时间：</b>{now} | <b>请求区间：</b>{period_desc} | <b>策略：</b>{', '.join(strategies)}</p>"

    if reports:
        all_starts = [r.get("backtest_start_ms") for r in reports if r.get("backtest_start_ms")]
        all_ends = [r.get("backtest_end_ms") for r in reports if r.get("backtest_end_ms")]
        if all_starts and all_ends:
            real_start = min(all_starts)
            real_end = max(all_ends)
            html += f"<p style='color:#666;'><b>📅 实际数据区间：</b>{fmt_ts(real_start)} ~ {fmt_ts(real_end)}（北京时间）</p>"

    # 一致性说明
    html += _render_consistency_notice(fee, capital)

    # 大数字汇总
    html += "<h3 style='margin-top:30px;'>📈 总览</h3>"
    html += "<div style='display:flex;flex-wrap:wrap;gap:12px;margin:15px 0;'>"
    cards = [
        ("测试标的", total, "#2c3e50"),
        ("有交易", len(with_trades), "#27ae60"),
        ("无交易", len(no_trades), "#95a5a6"),
        ("总交易笔数", total_trades, "#3498db"),
        ("平均收益", f"{avg_ret:+.2f}%", "#e74c3c" if avg_ret > 0 else "#27ae60"),
        ("平均超额", f"{avg_exc:+.2f}%", "#e74c3c" if avg_exc > 0 else "#27ae60"),
        ("平均胜率", f"{avg_win:.1f}%", "#9b59b6"),
    ]
    for label, value, color in cards:
        html += f"<div style='flex:1;min-width:110px;background:#fff;padding:15px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.08);text-align:center;'>"
        html += f"<div style='color:#888;font-size:12px;'>{label}</div>"
        html += f"<div style='color:{color};font-size:24px;font-weight:900;margin-top:6px;'>{value}</div>"
        html += "</div>"
    html += "</div>"

    # 方向统计
    html += "<div style='background:#fff;padding:15px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.08);font-size:14px;margin-top:15px;'>"
    html += f"<span style='color:#27ae60;'>🟢 <b>做多</b>：{total_long} 笔，胜率 {long_wr:.1f}%</span>"
    html += " &nbsp;&nbsp;|&nbsp;&nbsp; "
    html += f"<span style='color:#c0392b;'>🔴 <b>做空</b>：{total_short} 笔，胜率 {short_wr:.1f}%</span>"
    html += "</div>"

    # 评级分布
    if rating_counts:
        html += "<h3 style='margin-top:30px;'>🎖️ 评级分布</h3>"
        html += "<div style='background:#fff;padding:15px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.08);'>"
        for rating in ["🏆 S级", "🥇 A级", "🥈 B级", "🥉 C级", "❌ D级"]:
            n = rating_counts.get(rating, 0)
            if n > 0:
                html += f"<span style='display:inline-block;margin:6px 18px 6px 0;font-size:14px;'>{rating}: <b style='font-size:16px;'>{n}</b> 个</span>"
        html += "</div>"

    # Top 10 表格
    if top_n:
        html += "<h3 style='margin-top:30px;'>🏆 收益 Top 10</h3>"
        html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,0.08);font-size:13px;border-radius:10px;overflow:hidden;'>"
        html += "<tr style='background:#27ae60;color:#fff;'><th style='padding:10px;'>#</th><th>标的</th><th>交易数</th><th>胜率</th><th>收益</th><th>超额</th><th>回撤</th><th>夏普</th><th>评级</th></tr>"
        for i, r in enumerate(top_n, 1):
            html += f"<tr style='border-bottom:1px solid #eee;'>"
            html += f"<td style='padding:8px;text-align:center;'>{i}</td>"
            html += f"<td style='padding:8px;font-weight:bold;'>{r['symbol'].replace('_USDT','')}</td>"
            html += f"<td style='text-align:center;'>{r['total_trades']}</td>"
            html += f"<td style='text-align:center;'>{r['win_rate']}%</td>"
            html += f"<td style='text-align:center;color:#27ae60;font-weight:bold;'>{r['total_return']}%</td>"
            html += f"<td style='text-align:center;color:{'#27ae60' if r['excess_return']>0 else '#c0392b'};'>{r['excess_return']}%</td>"
            html += f"<td style='text-align:center;'>{r['max_dd']}%</td>"
            html += f"<td style='text-align:center;'>{r['sharpe']}</td>"
            html += f"<td style='text-align:center;'>{r['rating']}</td></tr>"
        html += "</table>"

    # Bottom 10 表格
    if bottom_n:
        html += "<h3 style='margin-top:30px;'>📉 收益 Bottom 10</h3>"
        html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,0.08);font-size:13px;border-radius:10px;overflow:hidden;'>"
        html += "<tr style='background:#c0392b;color:#fff;'><th style='padding:10px;'>标的</th><th>交易数</th><th>胜率</th><th>收益</th><th>超额</th><th>回撤</th><th>评级</th></tr>"
        for r in bottom_n:
            html += f"<tr style='border-bottom:1px solid #eee;'>"
            html += f"<td style='padding:8px;font-weight:bold;'>{r['symbol'].replace('_USDT','')}</td>"
            html += f"<td style='text-align:center;'>{r['total_trades']}</td>"
            html += f"<td style='text-align:center;'>{r['win_rate']}%</td>"
            html += f"<td style='text-align:center;color:#c0392b;font-weight:bold;'>{r['total_return']}%</td>"
            html += f"<td style='text-align:center;'>{r['excess_return']}%</td>"
            html += f"<td style='text-align:center;'>{r['max_dd']}%</td>"
            html += f"<td style='text-align:center;'>{r['rating']}</td></tr>"
        html += "</table>"

    # 明细
    if detail_list:
        html += f"<h3 style='margin-top:30px;'>🔍 Top {len(detail_list)} 标的详细分析</h3>"
        html += "<p style='color:#666;font-size:13px;'>每个标的展示完整交易明细（入场/出场时间、价格、仓位、盈亏、离场原因），可直接对照交易所 K 线核对。</p>"
        for r in detail_list:
            html += _render_single_detail(r, fee=fee, capital=capital)

    html += "<hr style='margin-top:40px;'><p style='color:#aaa;font-size:11px;text-align:center;'>参谋长全市场回测报告 · 仅供交流参考，不构成投资建议</p>"
    html += "</body></html>"
    return html


# ============================================================
# 单标的报告（custom 模式）
# ============================================================
def build_backtest_html(reports, strategies, symbols, period_desc, fee=0.0005, capital=10000):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")

    html = f"<html><body style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Arial,sans-serif;max-width:1100px;margin:auto;padding:20px;background:#f4f6f8;color:#2c3e50;'>"
    html += f"<h2 style='border-bottom:3px solid #e74c3c;padding-bottom:12px;'>📊 参谋长回测报告</h2>"
    html += f"<p style='color:#666;'><b>生成时间：</b>{now} | <b>请求区间：</b>{period_desc}</p>"
    html += f"<p style='color:#666;'><b>策略：</b>{', '.join(strategies)} | <b>标的：</b>{', '.join(symbols)}</p>"

    if reports:
        all_starts = [r.get("backtest_start_ms") for r in reports if r.get("backtest_start_ms")]
        all_ends = [r.get("backtest_end_ms") for r in reports if r.get("backtest_end_ms")]
        if all_starts and all_ends:
            real_start = min(all_starts)
            real_end = max(all_ends)
            html += f"<p style='color:#666;'><b>📅 实际数据区间：</b>{fmt_ts(real_start)} ~ {fmt_ts(real_end)}（北京时间）</p>"

    # 一致性说明
    html += _render_consistency_notice(fee, capital)

    # 总览对比表
    html += "<h3 style='margin-top:30px;'>📋 总览对比表</h3>"
    html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,0.08);font-size:13px;border-radius:10px;overflow:hidden;'>"
    html += "<tr style='background:#2c3e50;color:#fff;'>"
    html += "<th style='padding:10px;'>标的</th><th>数据源</th><th>交易</th><th>胜率</th>"
    html += "<th>策略收益</th><th>基准收益</th><th>超额</th><th>回撤</th><th>夏普</th><th>评级</th></tr>"
    for r in reports:
        excess_color = "#27ae60" if r["excess_return"] > 0 else "#c0392b"
        ret_color = "#27ae60" if r["total_return"] > 0 else "#c0392b"
        html += f"<tr style='border-bottom:1px solid #eee;'>"
        html += f"<td style='padding:10px;font-weight:bold;'>{r['symbol'].replace('_USDT','')}</td>"
        html += f"<td style='font-size:11px;color:#888;'>{r['data_source']}</td>"
        html += f"<td style='text-align:center;'>{r['total_trades']}</td>"
        html += f"<td style='text-align:center;'>{r['win_rate']}%</td>"
        html += f"<td style='text-align:center;color:{ret_color};font-weight:bold;'>{r['total_return']}%</td>"
        html += f"<td style='text-align:center;color:#666;'>{r['benchmark_return']}%</td>"
        html += f"<td style='text-align:center;color:{excess_color};font-weight:bold;'>{r['excess_return']}%</td>"
        html += f"<td style='text-align:center;'>{r['max_dd']}%</td>"
        html += f"<td style='text-align:center;'>{r['sharpe']}</td>"
        html += f"<td style='text-align:center;font-weight:bold;'>{r['rating']}</td>"
        html += "</tr>"
    html += "</table>"
    html += "<p style='color:#888;font-size:12px;margin-top:8px;'>💡 超额 = 策略收益 - 基准收益（买入持有）。超额为正 = 跑赢死拿</p>"

    # 每个标的的详细
    for r in reports:
        html += f"<hr style='margin:30px 0;'><h3>🔍 {r['symbol'].replace('_USDT','')} 详细分析</h3>"
        html += _render_single_detail(r, fee=fee, capital=capital)

    html += "<hr style='margin-top:40px;'><p style='color:#aaa;font-size:11px;text-align:center;'>本报告由参谋长回测系统自动生成，仅供交流参考</p>"
    html += "</body></html>"
    return html


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="custom", choices=["custom", "universe"])
    parser.add_argument("--strategies", nargs="*")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--max_symbols", type=int, default=150)
    parser.add_argument("--min_days", type=int, default=90)
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

    if args.mode == "universe":
        print(f"\n{'='*70}")
        print(f"🌐 全市场扫描模式")
        print(f"{'='*70}")
        universe = get_hyperliquid_universe()
        candidates = sorted([f"{c}_USDT" for c in universe])
        print(f"Hyperliquid 合约数：{len(candidates)}")

        if args.exclude:
            excludes = set(x.upper().replace("_USDT", "") for x in args.exclude.split())
            candidates = [s for s in candidates if s.replace("_USDT", "") not in excludes]
            print(f"排除 {len(excludes)} 个后：{len(candidates)}")

        candidates = filter_symbols_by_age(candidates, asset_type, args.min_days)
        symbols = candidates[:args.max_symbols]
        print(f"\n✅ 最终测试标的：{len(symbols)} 个")
    else:
        symbols = args.symbols or os.environ.get("BT_SYMBOLS", "").split() or d.get("symbols", ["BTC"])

    print(f"\n{'='*70}")
    print(f"📊 参谋长回测引擎 v5")
    print(f"   模式：{args.mode} | 策略：{strategies}")
    print(f"   标的数：{len(symbols)} | 区间：{period_desc}")
    print(f"   本金：{capital}U | 手续费：{fee*100}%")
    print(f"{'='*70}\n")

    all_reports = []
    t0 = time.time()
    for strat in strategies:
        for i, sym in enumerate(symbols, 1):
            print(f"\n[{i}/{len(symbols)}] {strat} | {sym} | 已用时 {int(time.time()-t0)}s")
            rep = run_single(strat, sym, asset_type, capital, fee, start_ms, end_ms)
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

    if args.mode == "universe":
        html = build_universe_backtest_html(all_reports, strategies, period_desc, fee=fee, capital=capital)
        subject = f"【参谋长全市场回测】{len(all_reports)}标的 | {with_trades}个有交易"
    else:
        html = build_backtest_html(all_reports, strategies, symbols, period_desc, fee=fee, capital=capital)
        now_str = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
        subject = f"【参谋长回测】{now_str} | {len(all_reports)}个标的"

    try:
        send_html_email(subject, html)
        print("\n📧 回测报告已发送")
    except Exception as e:
        print(f"\n❌ 邮件发送失败：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
