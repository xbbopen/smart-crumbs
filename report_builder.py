from datetime import datetime, timezone, timedelta
import random

BJT = timezone(timedelta(hours=8))

def translate_asset_type(asset_type):
    return {"futures": "合约", "spot": "现货"}.get(asset_type, asset_type)

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
    "资金费率百分位": "看谁在裸泳。费率极高说明多头拥挤，主力随时可能来个‘天地针’爆仓。注意：Hyperliquid每小时结算，正常费率在0.001%-0.005%，0.01%以上即为极端。",
    "RSI": "相对强弱指数。70以上是贪婪，35以下是恐慌。",
    "MA10": "短期生命线，站上偏强，跌破偏弱。",
    "ATR": "波动幅度，用来精准计算你的止损点和爆仓距离。",
    "4H趋势过滤": "逆势做单，死路一条。4小时方向不对，坚决不碰。",
}

# ================= 🚀 全新：中式美学 + 专属Logo头部 =================
AD_BANNER = """
<div style="background: linear-gradient(135deg, #d32f2f 0%, #b71c1c 50%, #880e4f 100%); border-radius: 12px; padding: 30px 20px; color: #fff; text-align: center; border: 2px solid #ffc107; box-shadow: 0 10px 25px rgba(183,28,28,0.4); position: relative; overflow: hidden;">
    <div style="position: absolute; top: -50px; left: -50px; width: 150px; height: 150px; background: rgba(255, 193, 7, 0.15); border-radius: 50%; filter: blur(30px);"></div>
    <div style="position: absolute; bottom: -50px; right: -50px; width: 150px; height: 150px; background: rgba(255, 193, 7, 0.15); border-radius: 50%; filter: blur(30px);"></div>
    
    <div style="display: inline-block; margin-bottom: 10px;">
        <svg width="70" height="70" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
            <rect x="5" y="5" width="90" height="90" rx="10" fill="#b71c1c" stroke="#ffc107" stroke-width="4"/>
            <path d="M30 40 Q50 20 70 40 L80 40 Q70 60 50 60 Q30 60 20 40 Z" fill="#ffc107"/>
            <circle cx="40" cy="35" r="3" fill="#b71c1c"/>
            <circle cx="60" cy="35" r="3" fill="#b71c1c"/>
            <path d="M45 45 Q50 50 55 45" fill="none" stroke="#b71c1c" stroke-width="3"/>
            <text x="50" y="80" font-family="'STKaiti', 'KaiTi', sans-serif" font-size="22" fill="#ffc107" text-anchor="middle" font-weight="bold">参谋长</text>
        </svg>
    </div>
    
    <h1 style="margin: 0; font-size: 38px; font-family: 'STKaiti', 'KaiTi', 'Microsoft YaHei', sans-serif; color: #ffd54f; text-shadow: 2px 2px 4px rgba(0,0,0,0.6); letter-spacing: 4px;">牛来参谋长</h1>
    <p style="font-size: 16px; margin: 12px 0 0 0; color: #fff; font-weight: bold; letter-spacing: 1px;">专抓暴涨暴跌 · 做没有感情的赚钱机器</p>
    <div style="margin-top: 18px; border-top: 1px solid rgba(255,255,255,0.2); padding-top: 12px; font-size: 14px; color: #ffecb3; font-weight: bold;">
        怕踏空？怕被割？关注参谋长，一起埋伏主力！
    </div>
</div>
"""

def get_leverage_advice(oi):
    if oi is None: return "数据受限，建议3x以下轻仓试错。"
    if oi > 500_000_000: return "【资金极度充裕】建议杠杆20x-50x（快进快出）。"
    if oi > 50_000_000: return "【流动性极佳】建议杠杆10x-20x。"
    if oi > 5_000_000: return "【中等流动性】建议杠杆5x-10x。"
    return "【流动性差】建议杠杆2x-3x（极易被插针，保命要紧）。"

def calc_trade_plan(direction, cp, atr, rh, rl):
    plan = {"stop": None, "risk_pct": 0, "position_pct": 0, "position_value": 0, "margin_10x": 0, "coin_amount": 0, "tp1_trigger": None, "tp1_stop": None, "tp2_trigger": None, "tp2_stop": None, "tp3_trigger": None, "tp3_stop": None}
    if direction == "short" and atr and rh:
        stop = rh + 2.0 * atr
        plan["stop"] = stop
        plan["risk_pct"] = (stop - cp) / cp
        plan["tp1_trigger"], plan["tp1_stop"] = cp - 1.0 * atr, cp
        plan["tp2_trigger"], plan["tp2_stop"] = cp - 2.0 * atr, cp - 1.0 * atr
        plan["tp3_trigger"], plan["tp3_stop"] = cp - 3.0 * atr, cp - 2.0 * atr
    elif direction and direction.startswith("long") and atr and rl:
        stop = rl - 2.0 * atr
        plan["stop"] = stop
        plan["risk_pct"] = (cp - stop) / cp
        plan["tp1_trigger"], plan["tp1_stop"] = cp + 1.0 * atr, cp
        plan["tp2_trigger"], plan["tp2_stop"] = cp + 2.0 * atr, cp + 1.0 * atr
        plan["tp3_trigger"], plan["tp3_stop"] = cp + 3.0 * atr, cp + 2.0 * atr
    if plan["risk_pct"] > 0:
        plan["position_pct"] = min(0.5, 0.02 / plan["risk_pct"])
        plan["position_value"] = 10000 * plan["position_pct"]
        plan["margin_10x"] = plan["position_value"] / 10
        plan["coin_amount"] = plan["position_value"] / cp if cp else 0
    return plan

def generate_commander_comment(r, is_triggered):
    md = r.get("market_data", {})
    sr_list = r.get("strategy_results", {})
    cp, rsi, adx, fp = r.get("current_price"), md.get("rsi"), md.get("adx"), md.get("funding_percentile")
    best_sr, max_score = None, -1
    for sname, sr in sr_list.items():
        if sr:
            for tk in ["track_1", "track_2", "track_3"]:
                score = sr.get(tk, {}).get("score", 0)
                if score > max_score: max_score, best_sr = score, sr
    comment = ""
    if is_triggered and best_sr:
        direction = best_sr.get("direction")
        if direction == "short":
            comment = f"兄弟们，{r['symbol']} 主力磨刀霍霍了！RSI飙到{rsi:.1f}，资金费率拥挤度{fp:.0%}，多头极其拥挤。这种时候就是主力准备‘一锅端’的信号！参谋长已经扣动扳机，准备迎接瀑布。记住，带好2%止损，别让到嘴的肉飞了！"
        elif direction == "long_rebound":
            comment = f"黄金坑来了！{r['symbol']} 暴跌洗盘结束，RSI砸到{rsi:.1f}，散户都在恐慌割肉，但CVD底背离已经暴露了主力吸筹的阴谋！这种带血的筹码，参谋长笑纳了。直接进场，目标反弹，拿住就是胜利！"
        elif direction == "long_trend":
            comment = f"{r['symbol']} 蓄力完毕，主力点火起飞！站上MA10，动能温和，主力扫货痕迹明显。这波趋势我们要吃满！带好止损，让利润奔跑，参谋长带你感受火箭升空的快感！"
        return f"<div style='background:#fff3cd; padding:12px; border-left:5px solid #e74c3c; margin-top:10px; border-radius:5px;'><b>🐮 参谋长解读：</b><span style='color:#c0392b; font-weight:bold;'>{comment}</span></div>"
    if best_sr:
        if adx is not None and adx < 20:
            comment = f"{r['symbol']} 现在ADX只有{adx:.1f}，主力在高度控盘，上下乱插针就是要把散户震出去。这种无序震荡，进去就是送人头。参谋长建议：管住手，等趋势明朗再动手！"
        elif rsi is not None and rsi >= 70:
            comment = f"注意风险！{r['symbol']} RSI高达{rsi:.1f}，价格已经在高位了。别被FOMO情绪冲昏头脑，现在追多就是接盘侠。参谋长在等它见顶信号，准备反手做空！"
        elif rsi is not None and rsi <= 35:
            comment = f"机会在酝酿！{r['symbol']} RSI已经砸到{rsi:.1f}，极度恐慌区。虽然还没到参谋长的开枪点位，但子弹已经上膛。一旦确认企稳，这里就是暴利反弹的起点！"
        elif fp is not None and fp >= 0.8:
            comment = f"{r['symbol']} 资金费率拥挤度达到了{fp:.0%}，多头太嗨了，这种时候往往危险。参谋长虽然暂时没开枪，但已经在找机会布局空单，准备收割这群狂欢的韭菜。"
        else:
            if max_score == 3: comment = f"马上要触发了！{r['symbol']} 各项指标都在临界点，主力意图已经暴露，参谋长正在死死盯盘。兄弟们保持关注，只要条件达标，我立马通知你们进场！"
            elif max_score == 2: comment = f"{r['symbol']} 盘面暗流涌动，虽然还没完全达标，但主力的小动作已经藏不住了。耐心是猎人最好的品质，等参谋长信号，我们一起精准狙击。"
            else: comment = f"{r['symbol']} 目前处于垃圾时间，各项指标都不达标，主力还在洗盘。参谋长从不打无准备之仗，空仓休息也是一种操作，等信号出来，我第一时间喊单！"
    return f"<div style='background:#f8f9fa; padding:12px; border-left:5px solid #3498db; margin-top:10px; border-radius:5px;'><b>🐮 参谋长解读：</b><span style='color:#2c3e50;'>{comment}</span></div>"

def build_symbol_block(r):
    md = r.get("market_data", {})
    cp = r.get("current_price")
    
    triggered_direction = None
    active_sr = None
    for sname, sr_obj in r.get("strategy_results", {}).items():
        if sr_obj and sr_obj.get("triggered"):
            triggered_direction = sr_obj.get("direction")
            active_sr = sr_obj
            break

    is_triggered = triggered_direction is not None

    if is_triggered:
        track_key = {"short":"track_2","long_trend":"track_1","long_rebound":"track_3"}.get(triggered_direction,"track_1")
        track_name = {"short":"🔪 见顶做空","long_trend":"🚀 底部突破做多","long_rebound":"🩸 暴跌反弹做多"}.get(triggered_direction,"未知")
        border_color = "#d32f2f"
        header_bg = "linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%)"
        badge_text = "🚨 开枪信号"
        badge_bg = "#ffc107"
        badge_color = "#b71c1c"
    else:
        border_color = "#34495e"
        header_bg = "linear-gradient(135deg, #2c3e50 0%, #1a252f 100%)"
        badge_text = "🔮 盘面推演"
        badge_bg = "#95a5a6"
        badge_color = "#ffffff"

    html = f"""
    <div style="border: 3px solid {border_color}; border-radius: 12px; margin-bottom: 30px; box-shadow: 0 6px 20px rgba(0,0,0,0.15); overflow: hidden;">
        <div style="background: {header_bg}; color: white; padding: 15px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid #ffc107;">
            <div>
                <span style="font-size: 28px; font-weight: 900; letter-spacing: 2px; color: #ffd54f; text-shadow: 1px 1px 3px rgba(0,0,0,0.5);">💥 {r['symbol'].replace('_USDT','')}</span>
                <span style="font-size: 16px; opacity: 0.9; margin-left: 10px; color: #fff;">{translate_asset_type(r.get('asset_type'))}</span>
            </div>
            <div style="background: {badge_bg}; color: {badge_color}; padding: 6px 15px; border-radius: 20px; font-weight: bold; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);">{badge_text}</div>
        </div>
        
        <div style="padding: 15px 20px; background: #f8f9fa; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; flex-wrap: wrap; font-size: 14px;">
            <div><b>当前价格：</b><span style="color: {border_color}; font-size: 1.2em; font-weight: bold;">${cp}</span></div>
            <div><b>数据源：</b>{md.get('data_source')}</div>
            <div><b>费率拥挤度：</b>{md.get('funding_percentile', 0):.0%}</div>
        </div>
    """
    
    if is_triggered:
        ta = active_sr.get(track_key, {})
        plan = calc_trade_plan(triggered_direction, cp, md.get("atr"), md.get("recent_high"), md.get("recent_low"))
        direction_label = "做多" if triggered_direction.startswith("long") else "做空"

        html += f"<div style='padding: 20px;'>"
        html += f"<h3 style='color: {border_color}; margin-top:0; font-size: 20px;'>✅ {track_name}（得分 {ta.get('score',0)}）</h3>"
        html += f"<p style='background:#fff0f0; padding:10px; border-radius:5px;'><b>参谋长理由：</b>{active_sr.get('reason','')}</p>"
        
        html += "<p style='font-weight:bold; border-bottom: 2px dashed #eee; padding-bottom:5px;'>🔍 狙击细节（主力底牌）：</p><ul style='font-size: 14px; color: #444;'>"
        for k,v in ta.get("details",{}).items():
            exp = CONDITION_EXPLANATIONS.get(k, "")
            html += f"<li style='margin-bottom:5px;'><b>{k}:</b> {v} <span style='color:#888;'>({exp})</span></li>"
        html += "</ul>"

        html += f"<div style='background:#fffbea; padding: 15px; border-left: 5px solid #f39c12; margin-top: 15px; border-radius: 0 8px 8px 0;'>"
        html += f"<h4 style='margin-top:0; color:#e67e22; font-size: 18px;'>🎯 参谋长具体操作指令（照着做）</h4>"
        html += f"<p><b>方向：</b>{direction_label} {r['symbol'].replace('_USDT','')} | <b>入场价：</b>${cp}</p>"
        if plan["stop"]:
            html += f"<p><b>2%资金管理（本金10000U）：</b>止损空间 {plan['risk_pct']*100:.2f}%，最大总仓位 <b>{plan['position_value']:.2f} USDT</b>。<br>10倍杠杆下，投入保证金 <b>{plan['margin_10x']:.2f} USDT</b>，开仓数量 <b>{plan['coin_amount']:.4f} 个</b>。</p>"
            html += f"<p><b>硬止损价：</b><span style='color:#d32f2f; font-weight:bold;'>${plan['stop']:.4f}</span></p>"
        if plan["tp1_trigger"]:
            html += f"<p><b>📐 三段式移动止盈（关键！）：</b><br>"
            html += f"1️⃣ 价格到 <b>${plan['tp1_trigger']:.4f}</b> 时，止损移至成本价 ${plan['tp1_stop']:.4f}<br>"
            html += f"2️⃣ 价格到 <b>${plan['tp2_trigger']:.4f}</b> 时，止损移至 ${plan['tp2_stop']:.4f}<br>"
            html += f"3️⃣ 价格到 <b>${plan['tp3_trigger']:.4f}</b> 时，止损移至 ${plan['tp3_stop']:.4f}，并建议止盈50%仓位</p>"
        html += f"<p><b>MA10动态离场线：</b>${md.get('ma10', 0):.4f}</p>"
        html += "</div>"
        html += generate_commander_comment(r, is_triggered=True)
        html += "</div>"
    else:
        html += f"<div style='padding: 20px;'>"
        html += f"<p style='color:#666; font-size:15px;'>当前标的尚未触发开枪信号，参谋长正在持续监控。以下是各项指标的推演情况：</p>"
        for sname, sr_obj in r.get("strategy_results", {}).items():
            if not sr_obj: continue
            html += f"<div style='margin-top:15px; padding-left:10px; border-left:4px solid #8e44ad; background:#fafafa; padding:10px; border-radius:0 5px 5px 0;'>"
            html += f"<p style='margin-top:0; font-weight:bold; color:#8e44ad;'>策略推演：{sname}</p>"
            for tk, tn in [("track_1","🚀 底部突破做多"),("track_2","🔪 见顶做空"),("track_3","🩸 暴跌反弹做多")]:
                ta = sr_obj.get(tk, {})
                hard = "✅ 硬条件通过" if ta.get("hard_ok") else "❌ 硬条件未过"
                score = ta.get('score', 0)
                max_score = 4 if tk in ['track_1','track_2'] else 3
                html += f"<p style='margin:5px 0;'><b>{tn}：{score}/{max_score} {hard}</b></p>"
                details = ta.get("details", {})
                if details:
                    html += "<ul style='color:#555; font-size:0.85em; margin-top:2px; margin-bottom:10px;'>"
                    for k, v in details.items():
                        exp = CONDITION_EXPLANATIONS.get(k, "")
                        html += f"<li><b>{k}:</b> {v} <span style='color:#999;'>({exp})</span></li>"
                    html += "</ul>"
            html += "</div>"
        html += generate_commander_comment(r, is_triggered=False)
        html += "</div>"
    
    html += "</div>"
    return html

def build_dashboard(results, triggered_list, untriggered_list):
    triggered_syms = [r['symbol'].replace('_USDT','') for r in triggered_list]
    untriggered_syms = [r['symbol'].replace('_USDT','') for r in untriggered_list if r.get("status") == "ok"]
    
    html = "<div style='background:#fff; border:2px solid #34495e; border-radius: 10px; padding: 15px; margin-bottom: 25px;'>"
    html += "<h3 style='margin-top:0; color:#2c3e50; border-bottom:2px dashed #eee; padding-bottom:10px;'>📋 本期监控全景图（直接定位你的标的）</h3>"
    
    # 🚀 修复：使用 join 字符串，避免嵌套 f-string 带来的反斜杠报错
    if triggered_syms:
        triggered_badges = "".join([f"<span style='background:#d32f2f;color:white;padding:3px 8px;border-radius:5px;font-weight:bold;margin:2px;'>{s}</span>" for s in triggered_syms])
        html += f"<p style='font-size: 16px; color: #e74c3c;'><b>🚨 触发信号：</b> {triggered_badges}</p>"
    else:
        html += f"<p style='font-size: 16px; color: #888;'><b>🚨 触发信号：</b> 无，参谋长正在耐心等待。</p>"
        
    if untriggered_syms:
        untriggered_badges = "".join([f"<span style='background:#ecf0f1;color:#2c3e50;padding:3px 8px;border-radius:5px;margin:2px;'>{s}</span>" for s in untriggered_syms])
        html += f"<p style='font-size: 14px; color: #555;'><b>🔮 盘面推演：</b> {untriggered_badges}</p>"
        
    html += "</div>"
    return html

def generate_dynamic_subject(triggered_list, untriggered_list, results):
    tr_syms = [r['symbol'].replace('_USDT','') for r in triggered_list]
    all_directions = []
    max_rsi, min_rsi = 0, 100
    max_fp = 0
    min_adx = 100

    for r in results:
        if r.get("status") != "ok": continue
        md = r.get("market_data", {})
        rsi, adx, fp = md.get("rsi"), md.get("adx"), md.get("funding_percentile")
        if rsi:
            max_rsi, min_rsi = max(max_rsi, rsi), min(min_rsi, rsi)
        if adx: min_adx = min(min_adx, adx)
        if fp: max_fp = max(max_fp, fp)
        for sr in r.get("strategy_results", {}).values():
            if sr and sr.get("triggered"): all_directions.append(sr.get("direction"))

    is_short = any(d == "short" for d in all_directions)
    is_long = any(d.startswith("long") for d in all_directions)

    if triggered_list:
        if len(triggered_list) >= 2:
            return random.choice([
                f"🚨【参谋长重磅战报】多空双杀！{', '.join(tr_syms[:3])}全线暴动！速看！",
                f"🔥【牛来参谋长】冰火两重天！多币种触发信号，主力底牌已被看穿！",
                f"💥【参谋长战报】大行情来了！{', '.join(tr_syms[:3])}齐爆，跟紧不迷路！"
            ])
        elif is_short and is_long:
            return random.choice([f"🚨【参谋长战报】多空双杀！主力露出獠牙，暴力行情一触即发！", f"🩸【牛来参谋长】一边逼空一边杀多，主力这波操作太狠了！"])
        elif is_short:
            return random.choice([f"🩸【参谋长战报】瀑布警告！主力磨刀霍霍，{tr_syms[0]}即将暴跌？", f"🔪【牛来参谋长】高位狂欢结束，{tr_syms[0]}即将一锅端！", f"⚠️【参谋长预警】极度贪婪！{tr_syms[0]}费率爆表，收割倒计时已开启！"])
        else:
            return random.choice([f"🚀【参谋长战报】火箭点火！{tr_syms[0]}暴力拉升启动！", f"💥【牛来参谋长】{tr_syms[0]}蓄力完毕，完美站上支撑，准备迎接财富列车！", f"🐮【参谋长预警】别踏空！{tr_syms[0]}突破在即，主力扫货痕迹明显！"])
    else:
        if min_adx < 20:
            return random.choice([f"⚠️【参谋长推演】大盘死水微澜？主力正在密谋大动作，散户千万别乱动！", f"🛡️【参谋长推演】ADX告急！主力高度控盘，此刻入场就是送人头！", f"🧐【牛来参谋长】盘面毫无波澜？越是平静，主力憋的大招越狠！"])
        elif max_rsi >= 70:
            return random.choice([f"🎈【参谋长预警】极度贪婪！RSI飙至{max_rsi:.0f}，主力随时准备一锅端！", f"🔪【参谋长推演】散户狂欢倒计时？参谋长已经悄悄架好空单！", f"🚨【牛来参谋长】RSI严重超买，这波追高的人，马上要吃苦头了！"])
        elif min_rsi <= 35:
            return random.choice([f"🩸【参谋长推演】极度恐慌！RSI砸至{min_rsi:.0f}，黄金坑正在悄悄形成？", f"💎【参谋长推演】带血的筹码满地都是，主力暗中吸筹，你慌了吗？", f"🐮【牛来参谋长】别人恐惧我贪婪，{min_rsi:.0f}的RSI，反弹还会远吗？"])
        elif max_fp >= 0.8:
            return random.choice([f"💥【参谋长推演】资金费率极度拥挤，多头太嗨了，瀑布随时降临！", f"⚠️【参谋长预警】费率爆表！主力准备收割韭菜，空头子弹已上膛！", f"🌪️【牛来参谋长】多头拥挤度{max_fp:.0%}，这是主力最爱的猎杀时刻！"])
        else:
            return random.choice([f"🔮【参谋长推演】主力正在密谋大动作？多空博弈白热化，散户请警惕！", f"🐮【参谋长推演】盘面暗流涌动，参谋长锁定猎物，等一个开枪信号！", f"🛡️【参谋长推演】没有感情的赚钱机器正在盯盘，主力意图已经暴露..."])

def build_report(results, active_strategies, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    triggered_list = []
    untriggered_list = []
    for r in results:
        has_triggered = any(sr and sr.get("triggered") for sr in r.get("strategy_results", {}).values())
        if has_triggered: triggered_list.append(r)
        else: untriggered_list.append(r)

    subject = generate_dynamic_subject(triggered_list, untriggered_list, results)

    html = f"<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#333;padding:10px; background-color:#f4f6f8;'>"
    html += AD_BANNER
    html += f"<div style='background:white; padding:20px; border-radius:10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);'>"
    html += f"<h2 style='border-bottom: 3px solid #e74c3c; padding-bottom: 10px; color:#2c3e50;'>📊 参谋长多策略监控报告</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} | <b>策略：</b>{', '.join(active_strategies)} | <b>数据源：</b>多源智能降级</p>"
    html += build_dashboard(results, triggered_list, untriggered_list)

    if triggered_list:
        html += "<h3 style='color:#e74c3c; border-left:5px solid #e74c3c; padding-left:10px; font-size:22px; margin-top:30px;'>🚨 参谋长开枪警告（引爆行情）</h3>"
        for r in triggered_list: html += build_symbol_block(r)
    
    if untriggered_list:
        html += "<hr><h3 style='color:#27ae60; border-left:5px solid #27ae60; padding-left:10px; font-size:20px; margin-top:30px;'>🔮 参谋长盘面推演（未触发，但暗流涌动）</h3>"
        for r in untriggered_list: html += build_symbol_block(r)

    html += build_unsupported_section(results)
    html += build_glossary_section()
    html += build_risk_warning()
    html += f"<p style='text-align:center; color:#e67e22; font-weight:bold; font-size:15px; margin-top:20px;'>👉 觉得有用？点赞、转发、关注“牛来参谋长”，带你一起埋伏主力！</p>"
    html += "</div></body></html>"
    return subject, html

def build_unsupported_section(results):
    unsupported = [r for r in results if r.get("status") == "unsupported"]
    if not unsupported: return ""
    html = "<hr><h3 style='color:#7f8c8d;'>⚠️ 暂不支持的标的</h3><ul>"
    for r in unsupported: html += f"<li>{r['symbol']}：该币种未上线合约，且现货数据源（币安/Gate.io）也无数据。</li>"
    html += "</ul>"
    return html

def build_glossary_section():
    html = "<hr><h3 style='color:#34495e; border-left:5px solid #34495e; padding-left:10px;'>📖 参谋长指标词典</h3>"
    html += "<div style='background:#f8f9fa; padding:15px; border-radius:8px; font-size:14px; color:#555;'>"
    for name, desc in SIGNAL_GLOSSARY.items(): html += f"<p style='margin: 8px 0;'><b>• {name}：</b>{desc}</p>"
    html += "</div>"
    return html

def build_risk_warning():
    return """
<div style="background: linear-gradient(135deg, #1a1a1a 0%, #2d1b1b 100%); color: #e0e0e0; padding: 25px; border-radius: 12px; margin-top: 25px; border: 1px solid #4a2c2c; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
    <h3 style="margin: 0 0 15px 0; color: #ffc107; font-size: 20px; text-align: center; letter-spacing: 2px;">⚠️ 参谋长最后说句掏心窝子的话</h3>
    <div style="border-left: 3px solid #d32f2f; padding-left: 15px; margin-bottom: 15px;">
        <p style="font-size: 15px; line-height: 1.8; margin: 0; color: #fff;">兄弟们，这份报告是参谋长用命换来的盯盘心血，但我不能替你扣扳机。</p>
    </div>
    <p style="font-size: 15px; line-height: 1.8; margin: 0 0 10px 0;">合约市场是个绞肉机。<b style="color: #ffc107;">90%的人死在这里，不是因为他们不够聪明，而是因为他们管不住手、舍不得止损、扛不住单。</b></p>
    <p style="font-size: 15px; line-height: 1.8; margin: 0 0 15px 0;"><b style="color: #ffc107;">参谋长给你的不是暴富密码，是一把刀。</b>刀怎么用，能不能活着走出来，看你自己。</p>
    <div style="background: rgba(211, 47, 47, 0.2); padding: 12px; border-radius: 8px; text-align: center;">
        <p style="font-size: 16px; line-height: 1.8; margin: 0; color: #ff5252; font-weight: bold;">记住：止损是你唯一的朋友。仓位是你唯一的铠甲。别让贪婪把你拖进深渊。</p>
    </div>
    <p style="font-size: 12px; line-height: 1.6; margin: 20px 0 0 0; color: #888; text-align: center;">本报告由AI系统自动生成，数据来源于公开市场，仅供交流参考，不构成任何投资建议。<br>加密货币交易具有极高风险，可能导致全部本金损失。参谋长只负责指明方向，扣动扳机前请三思。</p>
</div>
"""
