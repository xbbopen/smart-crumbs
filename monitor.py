# -*- coding: utf-8 -*-
import json, time, logging, sys, os
from datetime import datetime, timezone, timedelta

from strategies.loader import load_strategy
from data_fetcher import build_market_data, audit_watchlist
from report_builder import build_report
from email_sender import send_html_email

# ================= 日志配置 =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)
BJT = timezone(timedelta(hours=8))


def main():
    with open("config/config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    watchlist = config.get("watchlist", [])
    active_strategies = config.get("active_strategies", ["v1_default"])
    notify = config.get("notify_on_no_signal", True)

    log.info("=" * 60)
    log.info(f"📊 参谋长监控系统启动 | 共 {len(watchlist)} 个标的")
    log.info("=" * 60)

    # ============ 第一步：Hyperliquid 合约审计 ============
    log.info("🔍 正在对照 Hyperliquid 全币种，审计监控列表...")
    has_contract, no_contract = audit_watchlist(watchlist)

    log.info("-" * 60)
    log.info(f"✅ 有 Hyperliquid 合约 ({len(has_contract)}个): {', '.join(has_contract)}")
    if no_contract:
        log.info(f"⚠️  无 Hyperliquid 合约 ({len(no_contract)}个): {', '.join(no_contract)}")
        log.info(f"   ↳ 这些标的将直接走现货降级分析")
    log.info("-" * 60)

    # ============ 第二步：策略加载 ============
    strategies = {}
    for name in active_strategies:
        try:
            strategies[name] = load_strategy(name)
            log.info(f"✅ 策略加载成功: {name}")
        except Exception as e:
            log.error(f"❌ 加载策略失败 {name}: {e}")

    # ============ 第三步：逐个标的获取数据 ============
    all_results = []
    for idx, item in enumerate(watchlist, 1):
        sym, typ = item["symbol"], item.get("type", "futures")
        log.info(f"\n[{idx}/{len(watchlist)}] 处理 {sym} ({typ})")

        md, status = build_market_data(sym, typ)
        time.sleep(1.0)

        if status != "ok":
            all_results.append({
                "symbol": sym, "asset_type": typ,
                "status": status, "strategy_results": {}
            })
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

    # ============ 第四步：先写信号日志（无论邮件是否成功都不丢） ============
    write_signal_log(all_results)

    # ============ 第五步：再发邮件 ============
    any_triggered = any(
        sr and sr.get("triggered")
        for r in all_results
        for sr in r.get("strategy_results", {}).values()
    )

    email_ok = True
    if any_triggered or notify:
        subject, html = build_report(all_results, active_strategies, watchlist)
        try:
            send_html_email(subject, html)
        except Exception as e:
            email_ok = False
            log.error("=" * 60)
            log.error(f"❌ 邮件发送失败：{e}")
            log.error("常见原因：Gmail 主密码重置后，应用专用密码被自动撤销。")
            log.error("解决步骤：")
            log.error("  1. 打开 https://myaccount.google.com/apppasswords")
            log.error("  2. 生成新的 16 位应用专用密码")
            log.error("  3. 更新 GitHub Secrets 中的 SMTP_PASSWORD")
            log.error("=" * 60)
    else:
        log.info("无信号且 notify_on_no_signal=False，静默退出")

    # ============ 第六步：汇总输出 ============
    log.info("\n" + "=" * 60)
    log.info("📊 本次运行汇总")
    log.info("=" * 60)
    ok_count = sum(1 for r in all_results if r["status"] == "ok")
    triggered_count = sum(
        1 for r in all_results
        if any(sr and sr.get("triggered") for sr in r.get("strategy_results", {}).values())
    )
    log.info(f"✅ 成功获取: {ok_count}/{len(all_results)}")
    log.info(f"🚨 触发信号: {triggered_count}")
    log.info(f"📧 邮件发送: {'成功' if email_ok else '失败'}")
    log.info("=" * 60)

    # 邮件失败时让 job 明确标红，避免"静默成功"
    if not email_ok:
        sys.exit(1)


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
