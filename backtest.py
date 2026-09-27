import json, time, argparse, os, sys
from datetime import datetime, timezone, timedelta
from strategies.loader import load_strategy
from data_fetcher import fetch_hyperliquid_klines, fetch_binance_spot_klines, fetch_gateio_spot_klines, calc_ma, calc_atr, calc_rsi, calc_adx
from email_sender import send_html_email

BJT = timezone(timedelta(hours=8))

def parse_date(d):
    if not d: return None
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except:
        return None

def fetch_klines_for_backtest(symbol, asset_type, interval, limit, start_ms, end_ms):
    if asset_type == "futures":
        return fetch_hyperliquid_klines(symbol, interval, limit, start_ms, end_ms)
    else:
        # 现货：币安镜像 -> Gate.io -> Hyperliquid
        k = fetch_binance_spot_klines(symbol, interval, limit, start_ms, end_ms)
        if k: return k
        k = fetch_gateio_spot_klines(symbol, interval, limit)
        if k: return k
        return fetch_hyperliquid_klines(symbol, interval, limit, start_ms, end_ms)

def run_single(strategy_name, symbol, asset_type, interval, limit, capital, fee, start_ms, end_ms):
    klines = fetch_klines_for_backtest(symbol, asset_type, interval, limit, start_ms, end_ms)
    if not klines or len(klines) < 200:
        print(f"⚠️ {symbol} 数据不足")
        return None
    strategy = load_strategy(strategy_name)
    position, entry = None, 0
    total, wins, losses = 0, 0, 0
    max_cap, max_dd = capital, 0

    for i in range(150, len(klines)):
        window = klines[:i+1]
        md = {
            "symbol": f"{symbol}_USDT", "asset_type": asset_type,
            "current_price": window[-1]["close"], "klines": window,
            "klines_4h": [], "ma10": calc_ma(window,10), "atr": calc_atr(window,14),
            "rsi": calc_rsi(window,14), "adx": calc_adx(window,14),
            "recent_high": max(k["high"] for k in window[-150:-1]),
            "recent_low": min(k["low"] for k in window[-150:-1]),
            "funding_rate": None, "funding_percentile": None,
            "open_interest": None, "day_volume": None,
            "data_source": "backtest"
        }
        try:
            res = strategy.evaluate(symbol, asset_type, md)
        except:
            continue
        cp = window[-1]["close"]
        if res.get("triggered") and position is None:
            position, entry = res["direction"], cp
            total += 1
        elif position is not None:
            pnl_pct = (cp - entry)/entry if position.startswith("long") else (entry - cp)/entry
            if pnl_pct > 0.03 or pnl_pct < -0.02 or (i % 20 == 0):
                pnl = capital * pnl_pct - capital * fee * 2
                capital += pnl
                if pnl > 0: wins += 1
                else: losses += 1
                position = None
                max_cap = max(max_cap, capital)
                max_dd = max(max_dd, (max_cap - capital)/max_cap)
    if position:
        cp = klines[-1]["close"]
        pnl_pct = (cp - entry)/entry if position.startswith("long") else (entry - cp)/entry
        pnl = capital * pnl_pct - capital * fee * 2
        capital += pnl
        if pnl > 0: wins += 1
        else: losses += 1
    wr = wins/(wins+losses)*100 if (wins+losses)>0 else 0
    ret = (capital - 10000)/10000*100
    print(f"✅ {strategy_name} {symbol}: {total}笔, 胜率{wr:.1f}%, 收益{ret:.2f}%, 回撤{max_dd*100:.2f}%")
    return {
        "strategy": strategy_name, "symbol": symbol, "asset_type": asset_type,
        "total_trades": total, "wins": wins, "losses": losses, "win_rate": wr,
        "final_capital": capital, "total_return": ret, "max_dd": max_dd*100,
        "interval": interval, "start_ms": start_ms, "end_ms": end_ms
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="*")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--interval")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start_date")
    parser.add_argument("--end_date")
    args = parser.parse_args()

    with open("config/config.json") as f:
        cfg = json.load(f)
    d = cfg.get("backtest_defaults", {})
    strategies = args.strategies or os.environ.get("BT_STRATEGIES","").split() or d.get("strategies", ["v1_default"])
    symbols = args.symbols or os.environ.get("BT_SYMBOLS","").split() or d.get("symbols", ["BTC"])
    interval = args.interval or os.environ.get("BT_INTERVAL") or d.get("interval","30m")
    limit = args.limit or int(os.environ.get("BT_LIMIT","0")) or d.get("limit",1000)
    start_date = args.start_date or os.environ.get("BT_START_DATE") or d.get("start_date","")
    end_date = args.end_date or os.environ.get("BT_END_DATE") or d.get("end_date","")
    start_ms = parse_date(start_date)
    end_ms = parse_date(end_date)
    capital = d.get("initial_capital", 10000)
    fee = d.get("fee_rate", 0.0005)

    all_reports = []
    for strat in strategies:
        for sym in symbols:
            asset_type = "futures"  # 回测默认合约；如需现货请指定？暂且默认合约
            rep = run_single(strat, sym, asset_type, interval, limit, capital, fee, start_ms, end_ms)
            if rep: all_reports.append(rep)
            time.sleep(2)

    if not all_reports:
        print("无有效回测结果")
        return

    # 构建邮件
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    html = f"<html><body style='font-family:Arial;max-width:900px;margin:auto;'><h2>📊 参谋长回测报告</h2>"
    html += f"<p>时间：{now} | 周期：{interval} | 策略：{', '.join(strategies)} | 标的：{', '.join(symbols)}</p>"
    html += "<table border='1' cellpadding='8' style='border-collapse:collapse;width:100%;'>"
    html += "<tr style='background:#f0f0f0;'><th>策略</th><th>标的</th><th>交易次数</th><th>胜率</th><th>收益%</th><th>最大回撤%</th></tr>"
    for r in all_reports:
        color = "green" if r["total_return"] > 0 else "red"
        html += f"<tr><td>{r['strategy']}</td><td>{r['symbol']}</td><td>{r['total_trades']}</td><td>{r['win_rate']:.1f}%</td><td style='color:{color};'>{r['total_return']:.2f}%</td><td>{r['max_dd']:.2f}%</td></tr>"
    html += "</table>"
    if len(all_reports) > 1:
        by_s = {}
        for r in all_reports:
            by_s.setdefault(r["strategy"], []).append(r)
        html += "<h3>📈 按策略平均</h3><ul>"
        for s, reps in by_s.items():
            avg_wr = sum(x["win_rate"] for x in reps)/len(reps)
            avg_ret = sum(x["total_return"] for x in reps)/len(reps)
            avg_dd = sum(x["max_dd"] for x in reps)/len(reps)
            html += f"<li><b>{s}</b>：平均胜率{avg_wr:.1f}%，平均收益{avg_ret:.2f}%，平均回撤{avg_dd:.2f}%</li>"
        html += "</ul>"
    html += "<p style='color:#aaa;font-size:12px;'>本报告由参谋长回测系统自动生成</p></body></html>"
    subject = f"【参谋长回测】{now} | {len(all_reports)}个组合"
    send_html_email(subject, html)
    print("📧 回测报告已发送")

if __name__ == "__main__":
    main()
