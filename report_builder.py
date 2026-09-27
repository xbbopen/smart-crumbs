from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))

def translate_asset_type(asset_type):
    return {"futures": "合约", "spot": "现货"}.get(asset_type, asset_type)

# ================= 核心信号条件解释（用大白话+煽动性） =================
CONDITION_EXPLANATIONS = {
    "1.1-底部区域": "价格是否跌到了主力近期洗盘的底线（狙击区）",
    "1.2-站上MA10": "短期生命线是否收复（多军开始反击）",
    "1.3-RSI温和": "动能是否健康（没超买，弹药充足）",
    "1.4-放量确认": "底部有没有主力扫货（真金白银的买入）",
    "2.1-逼近高点": "价格是否已推到散户狂欢的悬崖边",
    "2.2-RSI超买": "是否极度狂热（聪明钱准备派发）",
    "2.3-CVD熊背离": "量价背离！价格在涨，但主力其实在偷偷出货",
    "2.4-费率极值": "做多的人太拥挤了，随时准备被主力一锅端",
    "3.1-大幅回撤": "是否跌出了黄金坑（空头动能衰竭）",
    "3.2-RSI超卖": "是否跌到了极度恐慌（散户割肉离场）",
    "3.3-CVD牛背离": "底背离！价格在跌，但主力资金已经在暗中吸筹",
    "市场状态": "当前市场是否具备单边行情的土壤（ADX趋势过滤）",
    "硬条件-4H": "大趋势是否顺风（4小时级别方向）",
    "硬条件-费率": "资金费率是否到了引爆点",
    "数据不足": "数据源获取失败（跳过本次推演）"
}

SIGNAL_GLOSSARY = {
    "ADX": "趋势强度指标。没有20以上的ADX，所有突破都可能是假动作。",
    "CVD": "量价背离神器。价格骗人，但资金的流向骗不了人。",
    "资金费率百分位": "看谁在裸泳。费率极高说明多头拥挤，主力随时可能来个‘天地针’爆仓。",
    "RSI": "相对强弱指数。70以上是贪婪，35以下是恐慌。",
    "MA10": "短期生命线，站上偏强，跌破偏弱。",
    "ATR": "波动幅度，用来精准计算你的止损点和爆仓距离。",
    "4H趋势过滤": "逆势做单，死路一条。4小时方向不对，坚决不碰。",
}

# ================= 极具煽动性和人设的广告位 =================
AD_BANNER = """
<div style="background: linear-gradient(135deg, #1e130c 0%, #9a8478 100%); color: white; padding: 25px; border-radius: 12px; text-align: center; margin-bottom: 20px; box-shadow: 0 10px 20px rgba(0,0,0,0.3);">
    <h1 style="margin: 0; font-size: 32px; letter-spacing: 3px; text-shadow: 2px 2px 4px #000;">🐮 牛来参谋长</h1>
    <p style="font-size: 16px; margin: 12px 0 8px 0; color: #f1c40f; font-weight: bold;">怕踏空？怕被割？专抓暴涨暴跌暴力反弹！</p>
    <p style="font-size: 18px; font-weight: bold; margin: 0; background: rgba(255,255,255,0.2); padding: 5px; border-radius: 5px;">参谋长预警系统，没有感情的赚钱机器。关注我，一起赚！</p>
</div>
"""

def get_leverage_advice(oi):
    if oi is None: return "数据受限，建议3x以下轻仓试错。"
    if oi > 500_000_000: return "【资金极度充裕】建议杠杆20x-50x（快进快出）。"
    if oi > 50_000_000: return "【流动性极佳】建议杠杆10x-20x。"
    if oi > 5_000_000: return "【中等流动性】建议杠杆5x-10x。"
    return "【流动性差】建议杠杆2x-3x（极易被插针，保命要紧）。"

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

def build_triggered_section(results):
    html = "<h3 style='color:#e74c3c; border-left:5px solid #e74c3c; padding-left:10px; font-size:22px;'>🚨 参谋长开枪警告（引爆行情）</h3>"
    found = False
    for r in results:
        if r.get("status") != "ok": continue
        for sname, sr in r.get("strategy_results", {}).items():
            if not sr or not sr.get("triggered"): continue
            found = True
            md = r.get("market_data", {})
            direction = sr.get("direction")
            track_key = {"short":"track_2","long_trend":"track_1","long_rebound":"track_3"}.get(direction,"track_1")
            track_name = {"short":"🔪 见顶做空（主力磨刀霍霍）","long_trend":"🚀 底部突破做多（主力点火起飞）","long_rebound":"🩸 暴跌反弹做多（黄金坑抢筹）"}.get(direction,"未知")
            ta = sr.get(track_key, {})
            cp = r.get("current_price")
            atr, ma10, rh, rl = md.get("atr"), md.get("ma10"), md.get("recent_high"), md.get("recent_low")
            plan = calc_trade_plan(direction, cp, atr, rh, rl)

            html += f"<div style='border:3px dashed #e74c3c; padding:18px; margin-bottom:20px; border-radius:10px; background:#fff5f5; box-shadow: 0 4px 15px rgba(231,76,60,0.2);'>"
            html += f"<h4 style='font-size: 22px; color: #c0392b; margin-top:0;'>💥 {r['symbol']} ({translate_asset_type(r.get('asset_type'))}) | 策略：{sname}</h4>"
            html += f"<p style='font-size:16px;'><b>当前价格：</b><span style='color:#e74c3c;font-size:1.2em;'>${cp}</span> | <b>数据源：</b>{md.get('data_source')}</p>"
            html += f"<p style='font-size: 20px; background:#e74c3c; color:white; padding:8px; border-radius:5px;'><b>{track_name} ！(得分 {ta.get('score',0)})</b></p>"
            html += f"<p style='font-size:16px;'><b>参谋长理由：</b>{sr.get('reason','')}</p>"
            
            html += "<p style='font-weight:bold;'>🔍 狙击细节（主力底牌）：</p><ul style='font-size:15px;'>"
            for k,v in ta.get("details",{}).items():
                exp = CONDITION_EXPLANATIONS.get(k, "")
                html += f"<li><b>{k}:</b> <span style='color:#d35400;'>{v}</span> <br><span style='color:#7f8c8d;font-size:0.85em;'>💡 参谋长解读：{exp}</span></li>"
            html += "</ul>"
            
            html += f"<div style='background:#fff; padding:15px; border-left:5px solid #f39c12; margin-top:15px; border-radius:0 8px 8px 0;'>"
            html += f"<h4 style='margin-top:0; color:#e67e22;'>🎯 参谋长财富密码（跟着做就行）</h4><ul style='font-size:16px;'>"
            html += f"<li><b>建议入场：</b><span style='font-weight:bold;'>${cp}</span></li>"
            if plan["stop"]: html += f"<li><b>止损防线：</b>${plan['stop']}（亏损风险{plan['risk_pct']*100:.2f}%，跌破必须走）</li>"
            html += f"<li><b>仓位建议：</b>{get_leverage_advice(md.get('open_interest'))}</li>"
            html += f"<li><b>2%资金管理：</b>总仓位不超过<span style='color:#e74c3c;font-weight:bold;'>{plan['position_pct']*100:.1f}%</span></li>"
            html += f"<li><b>实操落地：</b>以10000U本金、10倍杠杆为例，投入保证金{plan['margin_10x']:.2f}U，开仓{plan['coin_amount']:.4f}个{r['symbol'].replace('_USDT','')}。</li>"
            if cp: html += f"<li><b>止盈目标1（浮盈20%）：</b>${cp * 1.2}（先保本）</li><li><b>止盈目标2（浮盈50%）：</b>${cp * 1.5}（分批落袋）</li>"
            if ma10: html += f"<li><b>MA10动态离场线：</b>${ma10}（跌破立刻清仓）</li>"
            html += "</ul></div></div>"

    if not found:
        html += "<p style='color:#888; padding:10px; font-size:16px;'>🛡️ 当前主力还在洗盘，无明确开枪信号。参谋长正在紧盯盘面，兄弟们切勿盲目进场！</p>"
    return html

def build_untriggered_section(results):
    html = "<hr><h3 style='color:#27ae60; border-left:5px solid #27ae60; padding-left:10px; font-size:20px;'>🔮 参谋长盘面推演（未触发，但暗流涌动）</h3>"
    html += "<p style='color:#666; font-size:0.95em;'>以下是参谋长对当前盘面的全面推演。虽然还没到开枪时机，但主力的意图已经写在盘面上：</p>"
    
    for r in results:
        if r.get("status") != "ok":
            html += f"<p style='color:#999;'>🎯 <b>{r['symbol']}</b> — 数据异常（{r.get('status')}），主力高度控盘，暂不分析。</p>"
            continue
            
        md = r.get("market_data", {})
        cp = r.get("current_price")
        html += f"<div style='border:1px solid #ddd; padding:12px; margin-bottom:15px; border-radius:5px; background:#fafafa;'>"
        html += f"<h4 style='margin-top:0; color:#2c3e50;'>📌 {r['symbol']} ({translate_asset_type(r.get('asset_type'))}) | 现价：${cp} | 数据源：{md.get('data_source')}</h4>"
        if md.get("open_interest"):
            html += f"<p style='color:#e67e22; font-weight:bold;'>💡 参谋长流动性建议：{get_leverage_advice(md.get('open_interest'))}（24h成交额：{md.get('day_volume')}）</p>"
        
        for sname, sr in r.get("strategy_results", {}).items():
            if not sr: continue
            html += f"<div style='margin-top:10px; padding-left:10px; border-left:3px solid #8e44ad;'>"
            html += f"<p style='margin-bottom:5px;'><b>策略推演：{sname}</b></p>"
            
            for tk, tn in [("track_1","🚀 底部突破做多"),("track_2","🔪 见顶做空"),("track_3","🩸 暴跌反弹做多")]:
                ta = sr.get(tk, {})
                hard = "✅ 硬条件通过" if ta.get("hard_ok") else "❌ 硬条件未过（主力设下陷阱）"
                score = ta.get('score', 0)
                max_score = 4 if tk in ['track_1','track_2'] else 3
                
                html += f"<p style='margin:5px 0; font-size:0.95em;'><b>{tn}：{score}/{max_score} {hard}</b></p>"
                
                details = ta.get("details", {})
                if details:
                    html += "<ul style='color:#555; font-size:0.85em; margin-top:2px; margin-bottom:10px;'>"
                    for k, v in details.items():
                        exp = CONDITION_EXPLANATIONS.get(k, "")
                        exp_str = f" <span style='color:#999;'>（💡 参谋长：{exp}）</span>" if exp else ""
                        html += f"<li><b>{k}:</b> {v}{exp_str}</li>"
                    html += "</ul>"
                else:
                    html += "<p style='color:#999; font-size:0.85em;'>主力高度控盘，暂无详细数据</p>"
            html += "</div>"
        html += "</div>"
    return html

def build_unsupported_section(results):
    unsupported = [r for r in results if r.get("status") == "unsupported"]
    if not unsupported:
        return ""
    html = "<hr><h3 style='color:#7f8c8d;'>⚠️ 暂不支持的标的</h3><ul>"
    for r in unsupported:
        html += f"<li>{r['symbol']}：该币种未上线合约，且现货数据源（币安/Gate.io）也无数据。参谋长暂不关注。</li>"
    html += "</ul>"
    return html

def build_glossary_section():
    html = "<hr><h3 style='color:#34495e; border-left:5px solid #34495e; padding-left:10px;'>📖 参谋长指标词典</h3>"
    html += "<div style='background:#f8f9fa; padding:15px; border-radius:8px; font-size:14px; color:#555;'>"
    for name, desc in SIGNAL_GLOSSARY.items():
        html += f"<p style='margin: 8px 0;'><b>• {name}：</b>{desc}</p>"
    html += "</div>"
    return html

def build_report(results, active_strategies, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    
    any_triggered = any(
        sr and sr.get("triggered")
        for r in results
        for sr in r.get("strategy_results", {}).values()
    )
    
    # 在标题上直接制造悬念和话题
    if any_triggered:
        subject = f"🚨【参谋长战报】主力动手了！发现暴力做单机会！速看！"
    else:
        subject = f"🔮【参谋长推演】主力正在密谋大动作？多空博弈白热化，散户请警惕！"

    html = f"<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#333;padding:10px; background-color:#f4f6f8;'>"
    html += AD_BANNER
    html += f"<div style='background:white; padding:20px; border-radius:10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);'>"
    html += f"<h2 style='border-bottom: 3px solid #e74c3c; padding-bottom: 10px; color:#2c3e50;'>📊 参谋长多策略监控报告</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} | <b>策略：</b>{', '.join(active_strategies)} | <b>数据源：</b>多源智能降级</p>"
    
    html += build_triggered_section(results)
    html += build_untriggered_section(results)
    html += build_unsupported_section(results)
    html += build_glossary_section()
    
    html += "<hr><p style='text-align:center; color:#aaa; font-size:12px;'>本报告由牛来参谋长AI预警系统自动生成 | 数据仅供参考，不构成投资建议 | 合约交易风险极高，请严格设置止损</p>"
    html += f"<p style='text-align:center; color:#e67e22; font-weight:bold; font-size:14px;'>👉 觉得有用？点赞、转发、关注“牛来参谋长”，带你一起埋伏主力！</p>"
    html += "</div></body></html>"
    
    return subject, html
