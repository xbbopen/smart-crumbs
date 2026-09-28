import json, time, logging, sys, os
from datetime import datetime, timezone, timedelta
from strategies.loader import load_strategy
from data_fetcher import build_market_data
from report_builder import build_report
from email_sender import send_html_email

# ================= 日志配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)
BJT = timezone(timedelta(hours=8))

# ================= 主流程 =================
def main():
    with open("config/config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    watchlist = config.get("watchlist", [])
    active_strategies = config.get("active_strategies", ["v1_default"])
    notify = config.get("notify_on_no_signal", True)

    strategies = {}
    for name in active_strategies:
        try:
            strategies[name] = load_strategy(name)
        except Exception as e:
            log.error(f"加载策略失败 {name}: {e}")

    all_results = []
    for item in watchlist:
        sym, typ = item["symbol"], item.get("type", "futures")
        log.info(f"处理 {sym} ({typ})")
        
        # 🚀 修复点：build_market_data 现在返回 (md, status) 元组
        md, status = build_market_data(sym, typ)
        time.sleep(1.5) # 防限流
        
        if status != "ok":
            all_results.append({"symbol": sym, "asset_type": typ, "status": status, "strategy_results": {}})
            continue
            
        srs = {}
        for name, strat in strategies.items():
            try:
                srs[name] = strat.evaluate(sym, typ, md)
            except Exception as e:
                log.error(f"策略 {name} 评估 {sym} 异常: {e}")
                srs[name] = None
                
        all_results.append({
            "symbol": sym, "asset_type": typ, "status": "ok",
            "current_price": md.get("current_price"),
            "market_data": md,
            "strategy_results": srs
        })

    # 判断是否有任何标的触发信号
    any_triggered = any(sr and sr.get("triggered") for r in all_results for sr in r.get("strategy_results", {}).values())
    
    if any_triggered or notify:
        subject, html = build_report(all_results, active_strategies, watchlist)
        send_html_email(subject, html)
    else:
        log.info("无信号且 notify_on_no_signal=False，静默退出")

    # 记录信号日志
    write_signal_log(all_results)

# ================= 信号日志记录 =================
def write_signal_log(results):
    os.makedirs("logs", exist_ok=True)
    date_str = datetime.now(BJT).strftime("%Y%m%d")
    path = f"logs/signals_{date_str}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for r in results:
            for sname, sr in r.get("strategy_results", {}).items():
                if sr and sr.get("triggered"):
                    log_entry = {
                        "time": datetime.now(BJT).isoformat(),
                        "symbol": r["symbol"],
                        "strategy": sname,
                        "direction": sr.get("direction"),
                        "entry_price": r.get("current_price"),
                        "reason": sr.get("reason", "")
                    }
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()
