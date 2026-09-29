"""
回测引擎 v2 - 基于数据库多周期K线，完全复现实盘逻辑

核心改进：
1. 优先从 data/market.db 读取K线（复用已拉取的历史数据）
2. 多周期支持：30m + 1h + 4h + 1d，和实盘完全一致
3. 出场逻辑复现策略：ATR止损 + 三段式移动止盈 + MA10离场
4. 入场价使用 entry_plan 的加权均价
5. S/A/B/C/D 五级评级 + 按方向统计
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


def parse_date(d):
    if not d: return None
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except:
        return None


def build_market_data_from_slices(symbol, asset_type, window_30m, window_1h,
                                   window_4h, window_1d, data_mode, data_source):
    """
    从预切好的多周期K线组装 market_data。
    逻辑和 data_fetcher.build_market_data 的指标计算部分完全一致。
    """
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
        # 移动止盈
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
        # MA10离场
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


def run_single(strategy_name, symbol, asset_type, capital, fee, start_ms, end_ms):
    """单标的回测"""
    print(f"\n▶️  回测 {strategy_name} | {symbol}")

    # 1. 从数据库读取多周期K线
    klines_30m, info_30m = fetch_and_cache_klines(symbol, asset_type, "30m", 500)
    if not klines_30m:
        print(f"⚠️  {symbol} 30m 数据获取失败")
        return None
    klines_4h, info_4h = fetch_and_cache_klines(symbol, asset_type, "4h", 2000)

    # 2. 聚合 1h 和 1d
    klines_1h = aggregate_klines(klines_30m, 2)
    if not klines_4h:
        klines_4h = aggregate_klines(klines_30m, 8)
    klines_1d = aggregate_klines(klines_4h, 6)

    if len(klines_30m) < 250:
        print(f"⚠️  {symbol} 30m 数据不足（{len(klines_30m)}根），至少需要250根")
        return None

    # 3. 时间戳索引
    ts_1h = [k["timestamp"] for k in klines_1h]
    ts_4h = [k["timestamp"] for k in klines_4h]
    ts_1d = [k["timestamp"] for k in klines_1d]
    ts_30m = [k["timestamp"] for k in klines_30m]

    # 4. 确定回放范围
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

    print(f"   数据源：{info_30m['actual']} | 30m:{len(klines_30m)}根 | 4h:{len(klines_4h)}根 | 回放区间：第{start_idx}~{end_idx}根")

    strategy = load_strategy(strategy_name)
    data_mode = "futures" if info_30m["primary"] == "hyperliquid" else "spot"
    data_source = "Hyperliquid 合约" if data_mode == "futures" else "现货"

    # 5. 逐根回放
    trades = []
    position = None
    pos_info = None
    current_cap = capital

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

        # 持仓管理
        if position is not None:
            still_hold, exit_price, reason = manage_position(pos_info, cp, ma10, atr)
            if not still_hold:
                entry = pos_info["entry"]
                direction = pos_info["direction"]
                pnl_pct = (exit_price - entry) / entry if direction.startswith("long") else (entry - exit_price) / entry

                # 仓位价值 = 本金 × 2% / 风险比例
                risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
                position_value = min(current_cap * 0.5, current_cap * 0.02 / risk_pct) if risk_pct > 0 else current_cap * 0.5
                pnl = position_value * pnl_pct - position_value * fee * 2
                current_cap += pnl

                trades.append({
                    "entry_idx": pos_info["entry_idx"], "exit_idx": i,
                    "direction": direction,
                    "entry_price": entry, "exit_price": exit_price,
                    "pnl_pct": pnl_pct, "pnl_usd": pnl,
                    "bars_held": i - pos_info["entry_idx"],
                    "exit_reason": reason,
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
                    }

    # 6. 最后一根还持仓，按最后价平仓
    if position is not None and pos_info:
        cp = klines_30m[end_idx]["close"]
        entry = pos_info["entry"]
        direction = pos_info["direction"]
        pnl_pct = (cp - entry) / entry if direction.startswith("long") else (entry - cp) / entry
        risk_pct = abs(entry - pos_info["original_stop"]) / entry if entry else 0.02
        position_value = min(current_cap * 0.5, current_cap * 0.02 / risk_pct) if risk_pct > 0 else current_cap * 0.5
        pnl = position_value * pnl_pct - position_value * fee * 2
        current_cap += pnl
        trades.append({
            "entry_idx": pos_info["entry_idx"], "exit_idx": end_idx,
            "direction": direction,
            "entry_price": entry, "exit_price": cp,
            "pnl_pct": pnl_pct, "pnl_usd": pnl,
            "bars_held": end_idx - pos_info["entry_idx"],
            "exit_reason": "末尾平仓",
        })

    # 7. 计算指标
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

    # 按方向统计
    long_trades = [t for t in trades if t["direction"].startswith("long")]
    short_trades = [t for t in trades if t["direction"] == "short"]
    long_win_rate = len([t for t in long_trades if t["pnl_pct"] > 0]) / len(long_trades) * 100 if long_trades else 0
    short_win_rate = len([t for t in short_trades if t["pnl_pct"] > 0]) / len(short_trades) * 100 if short_trades else 0

    # 资金曲线
    cap = capital
    peak = cap
    max_dd = 0
    for t in trades:
        risk_pct = abs(t["entry_price"] - t["exit_price"]) / t["entry_price"] if t["entry_price"] else 0.02
        # 简化：直接用 pnl_usd 计算净值
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
        "trades": trades,
    }

    rating, rating_desc = evaluate_strategy(report)
    report["rating"] = rating
    report["rating_desc"] = rating_desc

    print(f"   ✅ {total}笔 | 胜率{win_rate:.1f}% | 收益{total_return:.2f}% | 基准{buy_hold:.2f}% | 超额{total_return-buy_hold:.2f}% | 夏普{sharpe:.2f} | {rating}")
    return report


def build_backtest_html(reports, strategies, symbols, period_desc):
    """生成回测HTML报告"""
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")

    html = f"<html><body style='font-family:Arial,sans-serif;max-width:1100px;margin:auto;padding:15px;background:#f4f6f8;'>"
    html += f"<h2 style='border-bottom:3px solid #e74c3c;padding-bottom:10px;'>📊 参谋长回测报告 v2</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} | <b>区间：</b>{period_desc}</p>"
    html += f"<p style='color:#666;'><b>策略：</b>{', '.join(strategies)} | <b>标的：</b>{', '.join(symbols)}</p>"
    html += f"<p style='color:#888;font-size:12px;'>回测基于数据库多周期K线，入场价采用策略给出的分档加权均价，出场采用ATR止损+三段式移动止盈+MA10离场。</p>"

    # 总览表
    html += "<h3>📋 总览对比表</h3>"
    html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,0.1);font-size:13px;'>"
    html += "<tr style='background:#2c3e50;color:#fff;'>"
    html += "<th style='padding:8px;'>标的</th><th>数据源</th><th>交易</th><th>胜率</th>"
    html += "<th>策略收益</th><th>基准收益</th><th>超额</th><th>回撤</th><th>夏普</th><th>评级</th></tr>"
    for r in reports:
        excess_color = "green" if r["excess_return"] > 0 else "red"
        ret_color = "green" if r["total_return"] > 0 else "red"
        html += f"<tr style='border-bottom:1px solid #eee;'>"
        html += f"<td style='padding:8px;font-weight:bold;'>{r['symbol']}</td>"
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
    html += "<p style='color:#888;font-size:12px;'>💡 超额 = 策略收益 - 基准收益（买入持有）。超额为正 = 跑赢死拿</p>"

    # 每个标的详细
    for r in reports:
        html += f"<hr style='margin:25px 0;'><h3>🔍 {r['symbol']} 详细分析</h3>"
        html += f"<div style='background:#fff;padding:15px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'>"
        html += f"<p style='font-size:16px;'><b>评级：</b><span style='color:#e67e22;font-size:18px;'>{r['rating']}</span> —— {r['rating_desc']}</p>"

        if r["total_trades"] == 0:
            html += f"<p style='color:#999;'>⚠️ 本区间未触发任何交易信号，策略在 {r['bars_total']} 根K线内保持空仓。</p>"
            html += f"<p>同期基准收益：<b>{r['benchmark_return']}%</b></p>"
        else:
            html += "<table style='width:100%;font-size:13px;'>"
            html += f"<tr><td style='padding:6px;'><b>交易次数</b></td><td>{r['total_trades']}</td>"
            html += f"<td style='padding:6px;'><b>胜率</b></td><td>{r['win_rate']}%</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>做多</b></td><td>{r['long_trades']}笔（胜率{r['long_win_rate']}%）</td>"
            html += f"<td style='padding:6px;'><b>做空</b></td><td>{r['short_trades']}笔（胜率{r['short_win_rate']}%）</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>平均赢单</b></td><td style='color:green;'>{r['avg_win']}%</td>"
            html += f"<td style='padding:6px;'><b>平均亏单</b></td><td style='color:red;'>{r['avg_loss']}%</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>盈亏比</b></td><td>{r['profit_factor']}</td>"
            html += f"<td style='padding:6px;'><b>平均持仓</b></td><td>{r['avg_bars_held']}根K线</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>夏普比率</b></td><td>{r['sharpe']}</td>"
            html += f"<td style='padding:6px;'><b>最大回撤</b></td><td>{r['max_dd']}%</td></tr>"
            html += "</table>"

            # 最近10笔交易
            recent = r["trades"][-10:]
            html += "<h4 style='margin-top:20px;'>📝 最近10笔交易</h4>"
            html += "<table style='width:100%;font-size:12px;border-collapse:collapse;'>"
            html += "<tr style='background:#f0f0f0;'><th>方向</th><th>入场价</th><th>出场价</th><th>盈亏%</th><th>盈亏U</th><th>持仓</th><th>离场原因</th></tr>"
            for t in recent:
                color = "green" if t["pnl_pct"] > 0 else "red"
                direction = "多" if t["direction"].startswith("long") else "空"
                html += f"<tr style='border-bottom:1px solid #f0f0f0;'>"
                html += f"<td style='text-align:center;'>{direction}</td>"
                html += f"<td style='text-align:center;'>${t['entry_price']:.4f}</td>"
                html += f"<td style='text-align:center;'>${t['exit_price']:.4f}</td>"
                html += f"<td style='text-align:center;color:{color};font-weight:bold;'>{t['pnl_pct']*100:.2f}%</td>"
                html += f"<td style='text-align:center;color:{color};'>{t['pnl_usd']:.2f}</td>"
                html += f"<td style='text-align:center;'>{t['bars_held']}</td>"
                html += f"<td style='text-align:center;font-size:11px;'>{t.get('exit_reason','')}</td></tr>"
            html += "</table>"

        html += "</div>"

    html += "<hr><p style='color:#aaa;font-size:11px;text-align:center;'>本报告由参谋长回测系统自动生成，仅供交流参考</p>"
    html += "</body></html>"
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="*")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--start_date")
    parser.add_argument("--end_date")
    parser.add_argument("--asset_type", default="futures")
    args = parser.parse_args()

    with open("config/config.json") as f:
        cfg = json.load(f)
    d = cfg.get("backtest_defaults", {})
    strategies = args.strategies or os.environ.get("BT_STRATEGIES", "").split() or d.get("strategies", ["v1_default"])
    symbols = args.symbols or os.environ.get("BT_SYMBOLS", "").split() or d.get("symbols", ["BTC"])
    start_date = args.start_date or os.environ.get("BT_START_DATE") or d.get("start_date", "")
    end_date = args.end_date or os.environ.get("BT_END_DATE") or d.get("end_date", "")
    asset_type = args.asset_type or os.environ.get("BT_ASSET_TYPE") or "futures"
    start_ms = parse_date(start_date)
    end_ms = parse_date(end_date)
    capital = d.get("initial_capital", 10000)
    fee = d.get("fee_rate", 0.0005)

    period_desc = f"{start_date or '最近'} 至 {end_date or '今日'}" if (start_date or end_date) else "全量数据"

    print("=" * 70)
    print(f"📊 参谋长回测引擎 v2")
    print(f"   策略：{strategies} | 标的：{symbols}")
    print(f"   区间：{period_desc} | 本金：{capital}U | 手续费：{fee*100}%")
    print("=" * 70)

    all_reports = []
    for strat in strategies:
        for sym in symbols:
            rep = run_single(strat, sym, asset_type, capital, fee, start_ms, end_ms)
            if rep:
                all_reports.append(rep)
            time.sleep(1)

    if not all_reports:
        print("\n❌ 无有效回测结果")
        sys.exit(1)

    html = build_backtest_html(all_reports, strategies, symbols, period_desc)
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【参谋长回测】{now} | {len(all_reports)}个标的"

    # 🚀 邮件失败必须抛出，让 GitHub Actions 明确标红
    try:
        send_html_email(subject, html)
        print("\n📧 回测报告已发送")
    except Exception as e:
        print(f"\n❌ 邮件发送失败：{e}")
        print("提示：Gmail 主密码重置会导致应用专用密码被撤销，")
        print("      请重新生成 16 位应用密码并更新 SMTP_PASSWORD。")
        sys.exit(1)


if __name__ == "__main__":
    main()
