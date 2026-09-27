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
    "硬条件-4H趋势": "大趋势是否顺风（4小时级别方向）",
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

# ================= 🚀 新增：参谋长专属点评引擎 =================
def generate_commander_comment(r, is_triggered):
    """根据标的数据，生成一段极具煽动性的参谋长点评"""
    md = r.get("market_data", {})
    sr_list = r.get("strategy_results", {})
    cp = r.get("current_price")
    rsi = md.get("rsi")
    adx = md.get("adx")
    fr = md.get("funding_rate")
    fp = md.get("funding_percentile")
    
    # 找出得分最高或已触发的策略结果
    best_sr = None
    max_score = -1
    for sname, sr in sr_list.items():
        if sr:
            for tk in ["track_1", "track_2", "track_3"]:
                score = sr.get(tk, {}).get("score", 0)
                if score > max_score:
                    max_score = score
                    best_sr = sr

    comment = ""
    
    # 1. 触发信号的点评（极度兴奋，强调机会与风控）
    if is_triggered and best_sr:
        direction = best_sr.get("direction")
        if direction == "short":
            comment = f"兄弟们，{r['symbol']} 主力磨刀霍霍了！RSI飙到{rsi:.1f}，资金费率百分位{fp:.0%}，多头极其拥挤。这种时候就是主力准备‘一锅端’的信号！参谋长已经扣动扳机，准备迎接瀑布。记住，带好2%止损，别让到嘴的肉飞了！"
        elif direction == "long_rebound":
            comment = f"黄金坑来了！{r['symbol']} 暴跌洗盘结束，RSI砸到{rsi:.1f}，散户都在恐慌割肉，但CVD底背离已经暴露了主力吸筹的阴谋！这种带血的筹码，参谋长笑纳了。直接进场，目标反弹，拿住就是胜利！"
        elif direction == "long_trend":
            comment = f"{r['symbol']} 蓄力完毕，主力点火起飞！站上MA10，动能温和，主力扫货痕迹明显。这波趋势我们要吃满！带好止损，让利润奔跑，参谋长带你感受火箭升空的快感！"
        return f"<div style='background:#fff3cd; padding:12px; border-left:5px solid #e74c3c; margin-top:10px; border-radius:5px;'><b>🐮 参谋长解读：</b><span style='color:#c0392b; font-weight:bold;'>{comment}</span></div>"

    # 2. 未触发信号的点评（冷静理智，指出隐患或等待机会）
    if best_sr:
        # 根据盘面情况给出不同点评
        if adx is not None and adx < 20:
            comment = f"{r['symbol']} 现在ADX只有{adx:.1f}，主力在高度控盘，上下乱插针就是要把散户震出去。这种无序震荡，进去就是送人头。参谋长建议：管住手，等趋势明朗再动手！"
        elif rsi is not None and rsi >= 70:
            comment = f"注意风险！{r['symbol']} RSI高达{rsi:.1f}，价格已经在高位了。别被FOMO情绪冲昏头脑，现在追多就是接盘侠。参谋长在等它见顶信号，准备反手做空！"
        elif rsi is not None and rsi <= 35:
            comment = f"机会在酝酿！{r['symbol']} RSI已经砸到{rsi:.1f}，极度恐慌区。虽然还没到参谋长的开枪点位，但子弹已经上膛。一旦确认企稳，这里就是暴利反弹的起点！"
        elif fp is not None and fp >= 0.8:
            comment = f"{r['symbol']} 资金费率拥挤度达到了{fp:.0%}，多头太嗨了，这种时候往往危险。参谋长虽然暂时没开枪，但已经在找机会布局空单，准备收割这群狂欢的韭菜。"
        else:
            # 通用等待文案
            if max_score == 3:
                comment = f"马上要触发了！{r['symbol']} 各项指标都在临界点，主力意图已经暴露，参谋长正在死死盯盘。兄弟们保持关注，只要条件达标，我立马通知你们进场！"
            elif max_score == 2:
                comment = f"{r['symbol']} 盘面暗流涌动，虽然还没完全达标，但主力的小动作已经藏不住了。耐心是猎人最好的品质，等参谋长信号，我们一起精准狙击。"
            else:
                comment = f"{r['symbol']} 目前处于垃圾时间，各项指标都不达标，主力还在洗盘。参谋长从不打无准备之仗，空仓休息也是一种操作，等信号出来，我第一时间喊单！"
    
    return f"<div style='background:#f8f9fa; padding:12px; border-left:5px solid #3498db; margin-top:10px; border-radius:5px;'><b>🐮 参谋长解读：</b><span style='color:#2c3e50;'>{comment}</span></div>"

# ================= 触发信号板块 =================
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
            html += "</ul></div>"
            
            # 插入参谋长解读
            html += generate_commander_comment(r, is_triggered=True)
            html += "</div>"

    if not found:
        html += "<p style='color:#888; padding:10px; font-size:16px;'>🛡️ 当前主力还在洗盘，无明确开枪信号。参谋长正在紧盯盘面，兄弟们切勿盲目进场！</p>"
    return html

# ================= 未触发信号板块 =================
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
        
        # 原始费率展示
        fr = md.get("funding_rate")
        fp = md.get("funding_percentile")
        if fr is not None:
            html += f"<p style='color:#e67e22; font-weight:bold;'>💡 当前资金费率：{fr:.5f}% （百分位：{fp:.0%}） | 流动性建议：{get_leverage_advice(md.get('open_interest'))}</p>"
        else:
            html += f"<p style='color:#e67e22; font-weight:bold;'>💡 流动性建议：{get_leverage_advice(md.get('open_interest'))}（24h成交额：{md.get('day_volume')}）</p>"
        
        for sname, sr in r.get("strategy_results", {}).items():
            if not sr: continue
            html += f"<div style='margin-top:10px; padding-left:10px; border-left:3px solid #8e44ad;'>"
            html += f"<p style='margin-bottom:5px;'><b>策略推演：{sname}</b></p>"
            
            for tk, tn in [("track_1","🚀 底部突破做多"),("track_2","🔪 见顶做空"),("track_3","🩸 暴跌反弹做多")]:
                ta = sr.get(tk, {})
                hard = "✅ 硬条件通过" if ta.get("hard_ok") else "❌ 硬条件未过"
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
            html += "</div>"
            
        # 插入参谋长解读
        html += generate_commander_comment(r, is_triggered=False)
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
