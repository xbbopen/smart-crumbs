"""
回测引擎 - 稳定获取数据 + 直观评估策略质量

核心改进：
1. 复用 build_market_data 逻辑，确保与实盘完全一致
2. 一次性拉取 Hyperliquid 全币种列表，本地判断是否有合约
3. 加入 Buy & Hold 基准收益、夏普比率、盈亏比
4. S/A/B/C/D 五级评级一眼看出策略好坏
"""
import json, time, argparse, os, math
from datetime import datetime, timezone, timedelta
from strategies.loader import load_strategy
from data_fetcher import (
    fetch_hyperliquid_klines, fetch_binance_spot_klines, fetch_gateio_spot_klines,
    calc_ma, calc_atr, calc_rsi, calc_adx,
    has_hyperliquid_contract, normalize_symbol
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


def fetch_data_for_backtest(symbol, asset_type, interval, limit, start_ms, end_ms):
    """
    数据获取。返回 (klines, data_mode, data_source, note)
    note: 说明文字，标注数据来源或跳过原因
    """
    if asset_type == "futures":
        # 先判断该币种在 Hyperliquid 上有没有合约
        if not has_hyperliquid_contract(symbol):
            note = f"Hyperliquid无{symbol}合约，回测跳过（如需测试现货请将asset_type改为spot）"
            print(f"⚠️  {note}")
            return None, None, None, note

        klines, status = fetch_hyperliquid_klines(symbol, interval, limit, start_ms, end_ms)
        if klines:
            return klines, "futures", "Hyperliquid 合约", ""
        if status == "rate_limited":
            note = f"Hyperliquid 429限流，跳过 {symbol}（请稍后重试）"
            print(f"⚠️  {note}")
            return None, None, None, note
        note = f"Hyperliquid 数据获取失败({status})"
        print(f"⚠️  {note}")
        return None, None, None, note
    else:
        klines, status = fetch_binance_spot_klines(symbol, interval, limit, start_ms, end_ms)
        if klines:
            return klines, "spot", "币安镜像 现货", ""
        klines, status = fetch_gateio_spot_klines(symbol, interval, limit)
        if klines:
            return klines, "spot", "Gate.io 现货", ""
        return None, None, None, f"现货数据获取失败({status})"


def calc_sharpe(returns, risk_free=0.0):
    """计算夏普比率。returns: 每笔交易的收益率列表"""
    if not returns or len(returns) < 2:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / len(returns)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (avg - risk_free) / std * math.sqrt(252)  # 年化


def evaluate_strategy(report):
    """根据回测结果给出 S/A/B/C/D 评级"""
    excess = report["total_return"] - report["benchmark_return"]
    sharpe = report["sharpe"]
    dd = report["max_dd"]
    trades = report["total_trades"]

    # 交易次数太少，评分降级
    if trades < 5:
        return "⚠️ 样本不足", "交易次数少于5次，回测结果不具参考性"

    # 综合评分
    score = 0
    if excess > 20: score += 40
    elif excess > 10: score += 30
    elif excess > 0: score += 20
    elif excess > -10: score += 10
    else: score += 0

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

    if score >= 85: return "🏆 S级", f"综合得分{score}。极强的盈利能力和风控，是顶级策略"
    if score >= 70: return "🥇 A级", f"综合得分{score}。稳定跑赢基准，策略有效"
    if score >= 55: return "🥈 B级", f"综合得分{score}。小幅跑赢基准，可优化"
    if score >= 40: return "🥉 C级", f"综合得分{score}。勉强跑平，需优化参数"
    return "❌ D级", f"综合得分{score}。跑输基准，建议弃用"


def run_single(strategy_name, symbol, asset_type, interval, limit, capital, fee, start_ms, end_ms):
    print(f"\n▶️  回测 {strategy_name} | {symbol} | {interval}")

    klines, data_mode, data_source, note = fetch_data_for_backtest(
        symbol, asset_type, interval, limit, start_ms, end_ms
    )
    if not klines:
        return None
    if len(klines) < 200:
        print(f"⚠️  {symbol} 数据不足（{len(klines)}根），跳过")
        return None

    print(f"   数据源：{data_source} | K线：{len(klines)}根")

    strategy = load_strategy(strategy_name)
    position, entry, entry_idx = None, 0, 0
    trades = []  # 记录每笔交易

    for i in range(150, len(klines)):
        window = klines[:i+1]
        md = {
            "symbol": f"{symbol}_USDT", "asset_type": asset_type, "data_mode": data_mode,
            "current_price": window[-1]["close"], "klines": window, "klines_4h": [],
            "ma10": calc_ma(window, 10), "atr": calc_atr(window, 14),
            "rsi": calc_rsi(window, 14), "adx": calc_adx(window, 14),
            "recent_high": max(k["high"] for k in window[-150:-1]),
            "recent_low": min(k["low"] for k in window[-150:-1]),
            "funding_rate": None, "funding_percentile": None,
            "open_interest": None, "day_volume": None,
            "data_source": data_source
        }
        try:
            res = strategy.evaluate(symbol, asset_type, md)
        except Exception:
            continue

        cp = window[-1]["close"]

        # 开仓
        if res.get("triggered") and position is None:
            position = res["direction"]
            entry = cp
            entry_idx = i

        # 平仓
        elif position is not None:
            pnl_pct = (cp - entry) / entry if position.startswith("long") else (entry - cp) / entry
            # 止盈+3% / 止损-2% / 强制平仓（每20根K线）
            if pnl_pct > 0.03 or pnl_pct < -0.02 or (i - entry_idx) >= 20:
                trades.append({
                    "entry_idx": entry_idx, "exit_idx": i,
                    "direction": position,
                    "entry_price": entry, "exit_price": cp,
                    "pnl_pct": pnl_pct, "bars_held": i - entry_idx
                })
                position = None

    # 最后还持仓，按最后价格平仓
    if position is not None:
        cp = klines[-1]["close"]
        pnl_pct = (cp - entry) / entry if position.startswith("long") else (entry - cp) / entry
        trades.append({
            "entry_idx": entry_idx, "exit_idx": len(klines) - 1,
            "direction": position,
            "entry_price": entry, "exit_price": cp,
            "pnl_pct": pnl_pct, "bars_held": len(klines) - 1 - entry_idx
        })

    # ============ 计算核心指标 ============
    total = len(trades)
    if total == 0:
        print(f"   ⚠️  未触发任何交易信号，说明策略在区间内非常保守")
        # 仍然计算基准收益
        buy_hold = (klines[-1]["close"] - klines[150]["close"]) / klines[150]["close"] * 100
        return {
            "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
            "data_mode": data_mode, "data_source": data_source,
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "final_capital": capital, "total_return": 0, "max_dd": 0,
            "benchmark_return": round(buy_hold, 2),
            "excess_return": round(-buy_hold, 2),
            "sharpe": 0, "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
            "avg_bars_held": 0, "rating": "⚠️ 无交易",
            "rating_desc": "策略在回测区间未触发任何信号，无法评估",
            "interval": interval, "bars_total": len(klines)
        }

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / total * 100
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t["pnl_pct"] for t in wins) / sum(t["pnl_pct"] for t in losses)) if losses else 999
    avg_bars_held = sum(t["bars_held"] for t in trades) / total

    # 收益率序列
    returns_seq = [t["pnl_pct"] for t in trades]
    sharpe = calc_sharpe(returns_seq)

    # 模拟净值曲线
    cap = capital
    peak = cap
    max_dd = 0
    for t in trades:
        cap *= (1 + t["pnl_pct"] - fee * 2)
        if cap > peak: peak = cap
        dd = (peak - cap) / peak
        if dd > max_dd: max_dd = dd

    total_return = (cap - capital) / capital * 100
    buy_hold = (klines[-1]["close"] - klines[150]["close"]) / klines[150]["close"] * 100

    report = {
        "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
        "data_mode": data_mode, "data_source": data_source,
        "total_trades": total, "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "final_capital": round(cap, 2),
        "total_return": round(total_return, 2),
        "max_dd": round(max_dd * 100, 2),
        "benchmark_return": round(buy_hold, 2),
        "excess_return": round(total_return - buy_hold, 2),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win * 100, 2),
        "avg_loss": round(avg_loss * 100, 2),
        "avg_bars_held": round(avg_bars_held, 1),
        "interval": interval, "bars_total": len(klines),
        "trades": trades  # 保留明细供报告展示
    }

    rating, rating_desc = evaluate_strategy(report)
    report["rating"] = rating
    report["rating_desc"] = rating_desc

    print(f"   ✅ {total}笔 | 胜率{win_rate:.1f}% | 收益{total_return:.2f}% | 基准{buy_hold:.2f}% | 超额{total_return-buy_hold:.2f}% | 夏普{sharpe:.2f} | {rating}")
    return report


def build_backtest_html(reports, strategies, symbols, interval, period_desc):
    """生成直观的回测HTML报告"""
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")

    html = f"<html><body style='font-family:Arial,sans-serif;max-width:1000px;margin:auto;padding:15px;background:#f4f6f8;'>"
    html += f"<h2 style='border-bottom:3px solid #e74c3c;padding-bottom:10px;'>📊 参谋长回测报告</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} | <b>周期：</b>{interval} | <b>区间：</b>{period_desc}</p>"
    html += f"<p style='color:#666;'><b>策略：</b>{', '.join(strategies)} | <b>标的：</b>{', '.join(symbols)}</p>"

    # ========== 总览表 ==========
    html += "<h3>📋 总览对比表</h3>"
    html += "<table style='width:100%;border-collapse:collapse;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,0.1);font-size:13px;'>"
    html += "<tr style='background:#2c3e50;color:#fff;'>"
    html += "<th style='padding:8px;'>标的</th><th>数据源</th><th>交易</th><th>胜率</th>"
    html += "<th>策略收益</th><th>基准收益</th><th>超额</th><th>回撤</th><th>夏普</th><th>评级</th></tr>"

    for r in reports:
        # 颜色：跑赢基准绿色，跑输红色
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
    html += "<p style='color:#888;font-size:12px;'>💡 超额 = 策略收益 - 基准收益（买入持有）。超额为正 = 策略跑赢死拿</p>"

    # ========== 单标的详细分析 ==========
    for r in reports:
        html += f"<hr style='margin:25px 0;'><h3>🔍 {r['symbol']} 详细分析</h3>"
        html += f"<div style='background:#fff;padding:15px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);'>"

        # 评级说明
        html += f"<p style='font-size:16px;'><b>评级：</b><span style='color:#e67e22;font-size:18px;'>{r['rating']}</span> —— {r['rating_desc']}</p>"

        if r["total_trades"] == 0:
            html += f"<p style='color:#999;'>⚠️ 本区间未触发任何交易信号。策略在 {r['bars_total']} 根K线内保持空仓。</p>"
            html += f"<p>同期基准收益（买入持有）：<b style='color:#666;'>{r['benchmark_return']}%</b></p>"
        else:
            # 分项指标
            html += "<table style='width:100%;font-size:13px;'>"
            html += f"<tr><td style='padding:6px;'><b>交易次数</b></td><td>{r['total_trades']}</td><td><b>胜率</b></td><td>{r['win_rate']}%</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>平均赢单</b></td><td style='color:green;'>{r['avg_win']}%</td><td><b>平均亏单</b></td><td style='color:red;'>{r['avg_loss']}%</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>盈亏比</b></td><td>{r['profit_factor']}</td><td><b>平均持仓</b></td><td>{r['avg_bars_held']}根K线</td></tr>"
            html += f"<tr><td style='padding:6px;'><b>夏普比率</b></td><td>{r['sharpe']}</td><td><b>最大回撤</b></td><td>{r['max_dd']}%</td></tr>"
            html += "</table>"

            # 最近10笔交易明细
            recent = r["trades"][-10:]
            html += "<h4 style='margin-top:20px;'>📝 最近10笔交易明细</h4>"
            html += "<table style='width:100%;font-size:12px;border-collapse:collapse;'>"
            html += "<tr style='background:#f0f0f0;'><th style='padding:4px;'>方向</th><th>入场价</th><th>出场价</th><th>盈亏%</th><th>持仓K线</th></tr>"
            for t in recent:
                color = "green" if t["pnl_pct"] > 0 else "red"
                direction = "多" if t["direction"].startswith("long") else ("逃顶" if t["direction"] == "spot_warning" else "空")
                html += f"<tr style='border-bottom:1px solid #f0f0f0;'>"
                html += f"<td style='text-align:center;'>{direction}</td>"
                html += f"<td style='text-align:center;'>${t['entry_price']:.4f}</td>"
                html += f"<td style='text-align:center;'>${t['exit_price']:.4f}</td>"
                html += f"<td style='text-align:center;color:{color};font-weight:bold;'>{t['pnl_pct']*100:.2f}%</td>"
                html += f"<td style='text-align:center;'>{t['bars_held']}</td></tr>"
            html += "</table>"

        html += "</div>"

    html += "<hr><p style='color:#aaa;font-size:11px;text-align:center;'>本报告由参谋长回测系统自动生成，数据来源于公开市场，仅供交流参考</p>"
    html += "</body></html>"
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="*")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--interval")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start_date")
    parser.add_argument("--end_date")
    parser.add_argument("--asset_type", default="futures")
    args = parser.parse_args()

    with open("config/config.json") as f:
        cfg = json.load(f)
    d = cfg.get("backtest_defaults", {})
    strategies = args.strategies or os.environ.get("BT_STRATEGIES", "").split() or d.get("strategies", ["v1_default"])
    symbols = args.symbols or os.environ.get("BT_SYMBOLS", "").split() or d.get("symbols", ["BTC"])
    interval = args.interval or os.environ.get("BT_INTERVAL") or d.get("interval", "30m")
    limit = args.limit or int(os.environ.get("BT_LIMIT", "0")) or d.get("limit", 1000)
    start_date = args.start_date or os.environ.get("BT_START_DATE") or d.get("start_date", "")
    end_date = args.end_date or os.environ.get("BT_END_DATE") or d.get("end_date", "")
    asset_type = args.asset_type or os.environ.get("BT_ASSET_TYPE") or "futures"
    start_ms = parse_date(start_date)
    end_ms = parse_date(end_date)
    capital = d.get("initial_capital", 10000)
    fee = d.get("fee_rate", 0.0005)

    period_desc = f"{start_date or '最近'} 至 {end_date or '今日'}" if (start_date or end_date) else f"最近{limit}根K线"

    print("=" * 70)
    print(f"📊 参谋长回测引擎")
    print(f"   策略：{strategies} | 标的：{symbols} | 周期：{interval}")
    print(f"   区间：{period_desc} | 本金：{capital}U | 手续费：{fee*100}%")
    print("=" * 70)

    all_reports = []
    for strat in strategies:
        for sym in symbols:
            rep = run_single(strat, sym, asset_type, interval, limit, capital, fee, start_ms, end_ms)
            if rep:
                all_reports.append(rep)
            time.sleep(2)  # 币种之间间隔

    if not all_reports:
        print("\n❌ 无有效回测结果")
        return

    # ========== 生成并发送报告 ==========
    html = build_backtest_html(all_reports, strategies, symbols, interval, period_desc)
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    subject = f"【参谋长回测】{now} | {len(all_reports)}个标的"
    send_html_email(subject, html)
    print("\n📧 回测报告已发送")


if __name__ == "__main__":
    main()
