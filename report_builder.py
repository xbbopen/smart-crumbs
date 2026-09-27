from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))

SIGNAL_GLOSSARY = {
    "ADX": "平均趋向指数，ADX<20代表无序震荡，所有趋势策略静默；ADX>25代表趋势强劲。",
    "CVD": "累积成交量差，价格新高但CVD未新高=买盘衰竭（熊背离）；价格新低但CVD回升=卖盘枯竭（牛背离）。",
    "资金费率百分位": "当前费率在历史中的相对位置，≥80%代表多头拥挤，是做空信号。",
    "RSI": "相对强弱指数，RSI≥70超买，RSI≤35超卖。",
    "MA10": "10周期均线，价格站上代表短期偏强。",
    "ATR": "平均真实波幅，用于止损和仓位计算。",
    "4H趋势过滤": "用4小时MA20判断中期趋势，逆势方向禁止开仓。",
}

AD_BANNER = """
<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; text-align: center; margin-bottom: 20px;">
    <h1 style="margin: 0; font-size: 28px;">🐮 牛来参谋长</h1>
    <p style="font-size: 16px; margin: 10px 0 0 0;">怕踏空？怕被割？牛来参谋长，专抓暴涨暴跌暴力反弹！</p>
    <p style="font-size: 18px; font-weight: bold; margin: 5px 0 0 0;">参谋长预警系统，没感情的赚钱机器。关注我，一起赚！</p>
</div>
"""

def get_leverage_advice(oi):
    if oi is None: return "OI数据缺失，建议3x以下轻仓。"
    if oi > 500_000_000: return "【极高流动性】建议杠杆20x-50x。"
    if oi > 50_000_000: return "【高流动性】建议杠杆10x-20x。"
    if oi > 5_000_000: return "【中等流动性】建议杠杆5x-10x。"
    return "【低流动性】建议杠杆2x-3x（极易插针）。"

def calc_trade_plan(direction, cp, atr, rh, rl):
    plan = {"stop": None, "risk_pct": 0, "position_pct": 0, "margin_10x": 0, "coin_amount": 0}
    if direction == "short" and atr and rh:
        stop = rh + 2.0 * atr
        plan["stop"], plan["risk_pct"] = stop, (stop - cp) / cp
    elif direction and direction.startswith("long") and atr and rl:
        stop = rl - 2.0 * atr
        plan["stop"], plan["risk_pct"] = stop, (cp - stop) / cp
    if plan["risk_pct"] > 0:
        plan["position_pct"] = min(1.0, 0.02 / plan["risk_pct"])
        pos_val = 10000 * 0.02 / plan["risk_pct"]
        plan["margin_10x"] = pos_val / 10
        plan["coin_amount"] = pos_val / cp if cp else 0
    return plan

def build_report(results, active_strategies, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    any_triggered = any(
        sr and sr.get("triggered")
        for r in results
        for sr in r.get("strategy_results", {}).values()
    )
    subject = f"【参谋长预警】{now} | {'🔥 发现暴力做单机会！' if any_triggered else '🛡️ 日常巡检报告'}"

    html = f"<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;padding:10px;'>"
    html += AD_BANNER
    html += f"<h2>📊 参谋长多策略监控报告</h2>"
    html += f"<p><b>时间：</b>{now} | <b>策略：</b>{', '.join(active_strategies)}</p><hr>"

    # 触发信号
    html += "<h3 style='color:#e74c3c;'>🚨 触发交易信号</h3>"
    found = False
    for r in results:
        if r.get("status") != "ok": continue
        for sname, sr in r.get("strategy_results", {}).items():
            if not sr or not sr.get("triggered"): continue
            found = True
            md = r.get("market_data", {})
            direction = sr.get("direction")
            track_key = {"short":"track_2","long_trend":"track_1","long_rebound":"track_3"}.get(direction,"track_1")
            track_name = {"short":"见顶做空","long_trend":"底部突破做多","long_rebound":"暴跌反弹做多"}.get(direction,"未知")
            ta = sr.get(track_key, {})
            cp = r.get("current_price")
            atr, ma10, rh, rl = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low")
            plan = calc_trade_plan(direction, cp, atr, rh, rl)

            html += f"<div style='border:3px solid #e74c3c; padding:15px; margin-bottom:20px; border-radius:8px; background:#fff9f9;'>"
            html += f"<h4>🎯 {r['symbol']} ({r.get('asset_type')}) | 策略：{sname}</h4>"
            html += f"<p>当前价：${cp} | 数据源：{md.get('data_source')}</p>"
            html += f"<p><b>✅ 触发策略：{track_name}（得分 {ta.get('score',0)}）</b></p>"
            html += f"<p><b>原因：</b>{sr.get('reason','')}</p>"
            html += "<ul>"
            for k,v in ta.get("details",{}).items():
                html += f"<li><b>{k}:</b> {v}</li>"
            html += "</ul>"
            html += f"<div style='background:#f0f8ff; padding:10px; border-left:5px solid #2980b9;'>"
            html += f"<p><b>📐 交易计划</b></p><ul>"
            html += f"<li>入场：${cp}</li>"
            if plan["stop"]: html += f"<li>止损：${plan['stop']}（风险{plan['risk_pct']*100:.2f}%）</li>"
            html += f"<li>流动性建议：{get_leverage_advice(md.get('open_interest'))}</li>"
            html += f"<li>2%仓位建议：总仓位不超过{plan['position_pct']*100:.1f}%</li>"
            html += f"<li>10000U本金10倍杠杆：保证金{plan['margin_10x']:.2f}U，开仓{plan['coin_amount']:.4f}个</li>"
            html += "</ul></div></div>"

    if not found:
        html += "<p style='color:#888;'>当前无标的触发交易信号。</p>"

    # 未触发
    html += "<hr><h3 style='color:#27ae60;'>⏳ 未触发标的详情</h3>"
    for r in results:
        if r.get("status") != "ok":
            html += f"<p style='color:#999;'>{r['symbol']} — 数据异常（{r.get('status')}）</p>"
            continue
        md = r.get("market_data", {})
        cp = r.get("current_price")
        html += f"<div style='border:1px solid #ddd; padding:10px; margin-bottom:10px;'>"
        html += f"<h4>{r['symbol']} ({r.get('asset_type')}) | 现价${cp} | 源:{md.get('data_source')}</h4>"
        if md.get("open_interest"):
            html += f"<p style='color:#e67e22;'>💡 {get_leverage_advice(md.get('open_interest'))}</p>"
        for sname, sr in r.get("strategy_results", {}).items():
            if not sr: continue
            html += f"<p><b>策略：{sname}</b></p><ul style='font-size:0.9em;color:#555;'>"
            for tk, tn in [("track_1","底部突破做多"),("track_2","见顶做空"),("track_3","暴跌反弹做多")]:
                ta = sr.get(tk, {})
                hard = "✅硬条件通过" if ta.get("hard_ok") else "❌硬条件未过"
                html += f"<li>{tn}：{ta.get('score',0)}/{'4' if tk in ['track_1','track_2'] else '3'} {hard}</li>"
            html += "</ul>"
        html += "</div>"

    # 指标详解
    html += "<hr><h3>📖 指标详解</h3><div style='background:#f8f9fa;padding:15px;font-size:14px;color:#555;'>"
    for n,d in SIGNAL_GLOSSARY.items():
        html += f"<p><b>• {n}：</b>{d}</p>"
    html += "</div>"
    html += "<hr><p style='text-align:center;color:#aaa;font-size:12px;'>本报告由参谋长AI系统生成 | 仅供参考 | 合约风险极高</p></body></html>"
    return subject, html
