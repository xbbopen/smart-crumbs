from datetime import datetime, timezone, timedelta
import random

BJT = timezone(timedelta(hours=8))

def translate_asset_type(asset_type):
    return {"futures": "合约", "spot": "现货"}.get(asset_type, asset_type)

def translate_trend(t):
    return {"up": "🟢 多头", "down": "🔴 空头", "neutral": "⚪ 震荡", None: "❓ 未知"}.get(t, "❓ 未知")

CONDITION_EXPLANATIONS = {
    "硬条件": "该轨道能否开单的前置门槛",
    "1.底部区域": "价格是否跌到了主力近期洗盘的底线",
    "2.站上30m MA10": "短期生命线是否收复",
    "3.30m RSI温和 或 1H超卖": "动能是否健康，或1H已到超卖区",
    "4.30m放量阳线": "底部是否有主力真金白银的买入",
    "5.1H KDJ超卖": "1H级别短期抛压是否耗尽",
    "6.4H MACD金叉": "4H动量是否转多",
    "A1.逼近高点": "价格是否已推到散户狂欢的悬崖边",
    "A2.30m RSI超买": "30m是否极度狂热",
    "A3.30m CVD顶背离": "量价背离，主力暗中出货",
    "A4.1H RSI超买或顶背离": "1H是否极度狂热或出现顶背离",
    "A5.1H KDJ超买": "1H短期是否过热",
    "A6.4H MACD死叉": "4H动量是否转空",
    "B1.4H空头排列": "4H EMA20 < EMA50，趋势向下",
    "B2.跌破30m MA10": "短期生命线已失守",
    "B3.回撤区间": "价格处于下跌中继的舒适追空区（5%-15%）",
    "B4.30m反弹遇阻": "反弹时出现长上影或看跌吞没",
    "B5.1H MACD柱为负": "1H动量偏空",
    "B6.1H RSI偏弱": "1H RSI < 50，偏空",
    "1.大幅回撤": "是否跌出黄金坑",
    "2.30m RSI超卖": "30m是否极度恐慌",
    "3.30m CVD牛背离": "价跌但主力暗中吸筹",
    "4.1H RSI超卖或底背离": "1H是否极度恐慌或出现底背离",
    "5.1H KDJ超卖": "1H抛压耗尽",
    "6.30m止跌形态": "出现锤头线或看涨吞没",
    "1.回踩1H布林中轨": "价格回踩到1H布林中轨附近（±2%）",
    "2.缩量回踩": "回踩时成交量萎缩（卖压衰竭）",
    "3.30m止跌形态": "出现止跌K线",
    "4.1H RSI健康": "1H RSI在40-55之间",
    "5.站上30m MA10": "短期生命线收复",
    "6.4H MACD健康": "4H MACD未死叉",
}

SIGNAL_GLOSSARY = {
    "多周期共振": "1D定大方向，4H定中期趋势，1H找入场时机，30m精确扣扳机。",
    "ADX": "趋势强度。轨道1/2要求≥20，轨道3放宽到≥12。",
    "CVD": "累积成交量差。价格新高但CVD未新高=买盘衰竭（顶背离）。",
    "RSI背离": "价格新高但RSI未新高=顶背离；价格新低但RSI未新低=底背离。",
    "资金费率": "Hyperliquid每小时结算，正常0.001%-0.003%，0.005%以上警告，0.01%以上极端。",
    "KDJ": "J值<0超卖，>100超买，适合捕捉短期极端。",
    "布林中轨": "1H的MA20，趋势回踩的经典支撑位。",
    "ATR": "波动幅度，用于止损和仓位计算。",
}

AD_BANNER = """
<div style="text-align: center; margin-bottom: 20px;">
    <img src="https://raw.githubusercontent.com/xbbopen/smart-crumbs/main/banner.png" 
         alt="牛来参谋长" loading="lazy" decoding="async"
         style="width: 100%; max-width: 800px; height: auto; border-radius: 12px; display: block; margin: 0 auto; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
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
    elif direction and (direction.startswith("long") or direction == "spot_warning") and atr and rl:
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

# ================= 🚀 多周期面板渲染 =================
def render_multi_tf_panel(md):
    """多周期状态表 + 各周期做单提示"""
    trend_1d = md.get("trend_1d")
    trend_4h = md.get("trend_4h")
    rsi_1h = md.get("rsi_1h")
    rsi_4h = md.get("rsi_4h")
    rsi_1d = md.get("rsi_1d")
    kdj_1h = md.get("kdj_1h") or {}
    boll_1h = md.get("boll_1h") or {}
    macd_1h = md.get("macd_1h") or {}
    macd_4h = md.get("macd_4h") or {}
    ema50_1d = md.get("ema50_1d")
    ema20_4h = md.get("ema20_4h")
    ema50_4h = md.get("ema50_4h")
    price = md.get("current_price")
    rsi_div = md.get("rsi_div_1h")

    html = "<div style='background:#f8f9fa; padding:15px; border-radius:8px; margin-top:15px; border:1px solid #eee;'>"
    html += "<h4 style='margin:0 0 12px 0; color:#2c3e50;'>🌐 多周期共振面板</h4>"
    html += "<table style='width:100%; font-size:13px; border-collapse:collapse;'>"
    html += "<tr style='background:#e8e8e8;'><th style='padding:6px; text-align:left;'>周期</th><th style='padding:6px;'>趋势</th><th style='padding:6px;'>RSI</th><th style='padding:6px;'>关键位/信号</th></tr>"

    # 1D
    day_signal = "N/A"
    if ema50_1d:
        day_signal = f"EMA50={ema50_1d:.4f} | 价格{'上方' if price and price > ema50_1d else '下方'}"
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>📅 日线</td>"
    html += f"<td style='text-align:center;'>{translate_trend(trend_1d)}</td>"
    html += f"<td style='text-align:center;'>{f'{rsi_1d:.1f}' if rsi_1d else 'N/A'}</td>"
    html += f"<td style='text-align:center; font-size:12px;'>{day_signal}</td></tr>"

    # 4H
    h4_signal = []
    if ema20_4h and ema50_4h:
        h4_signal.append(f"EMA20={ema20_4h:.4f}")
        h4_signal.append(f"EMA50={ema50_4h:.4f}")
    if macd_4h.get("hist") is not None:
        h4_signal.append(f"MACD柱={'正' if macd_4h['hist']>0 else '负'}")
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>⏰ 4小时</td>"
    html += f"<td style='text-align:center;'>{translate_trend(trend_4h)}</td>"
    html += f"<td style='text-align:center;'>{f'{rsi_4h:.1f}' if rsi_4h else 'N/A'}</td>"
    html += f"<td style='text-align:center; font-size:12px;'>{' | '.join(h4_signal) if h4_signal else 'N/A'}</td></tr>"

    # 1H
    h1_signal = []
    if boll_1h.get("mid"):
        h1_signal.append(f"BOLL中轨={boll_1h['mid']:.4f}")
    if kdj_1h.get("j") is not None:
        h1_signal.append(f"KDJ J={kdj_1h['j']:.1f}")
    if rsi_div:
        h1_signal.append(f"RSI背离={rsi_div}")
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>🕐 1小时</td>"
    html += f"<td style='text-align:center;'>-</td>"
    html += f"<td style='text-align:center;'>{f'{rsi_1h:.1f}' if rsi_1h else 'N/A'}</td>"
    html += f"<td style='text-align:center; font-size:12px;'>{' | '.join(h1_signal) if h1_signal else 'N/A'}</td></tr>"

    # 30m
    m30_signal = []
    m30_ma10 = md.get("ma10")
    m30_adx = md.get("adx")
    if m30_ma10:
        m30_signal.append(f"MA10={m30_ma10:.4f}")
    if m30_adx is not None:
        m30_signal.append(f"ADX={m30_adx:.1f}")
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>⏱️ 30分钟</td>"
    html += f"<td style='text-align:center;'>-</td>"
    html += f"<td style='text-align:center;'>{f'{md.get(\"rsi\"):.1f}' if md.get('rsi') else 'N/A'}</td>"
    html += f"<td style='text-align:center; font-size:12px;'>{' | '.join(m30_signal) if m30_signal else 'N/A'}</td></tr>"
    html += "</table>"

    # 综合做单提示
    html += "<div style='margin-top:12px; padding:10px; background:#fff; border-left:4px solid #3498db; border-radius:0 5px 5px 0;'>"
    html += "<p style='margin:0; font-size:13px; color:#2c3e50;'><b>💡 参谋长多周期提示：</b>"
    tips = []
    if trend_1d == "up": tips.append("日线多头，回调是机会")
    elif trend_1d == "down": tips.append("日线空头，反弹是陷阱")
    else: tips.append("日线震荡，区间操作")
    if trend_4h == "up" and trend_1d == "up": tips.append("4H共振多头，优先做多")
    elif trend_4h == "down" and trend_1d == "down": tips.append("4H共振空头，优先做空")
    if rsi_1h and rsi_1h < 35: tips.append("1H超卖，等止跌")
    elif rsi_1h and rsi_1h > 70: tips.append("1H超买，等回落")
    html += "；".join(tips) + "。</p></div>"
    html += "</div>"
    return html

# ================= 参谋长行情阶段推演器 =================
def generate_market_stage(r):
    md = r.get("market_data", {})
    cp = r.get("current_price")
    ma10 = md.get("ma10")
    rh = md.get("recent_high")
    rl = md.get("recent_low")
    trend_1d = md.get("trend_1d")
    trend_4h = md.get("trend_4h")

    if cp is None or rl is None or rh is None:
        return "数据不足", "无法推演当前阶段。"

    if trend_1d == "up" and trend_4h == "up":
        if ma10 and cp < ma10 and cp > rl * 1.05:
            return "🔥 多头回踩期", f"1D+4H双多头共振，价格回踩至生命线下方。这是经典的'趋势回踩'黄金坑，等30m止跌即可入场。"
        else:
            return "🚀 多头趋势期", f"1D+4H双多头共振，趋势健康。回调即买入机会，坚决不做空。"
    elif trend_1d == "down" and trend_4h == "down":
        return "🔪 空头趋势期", f"1D+4H双空头共振，反弹即做空机会。绝对不要抄底，等暴跌后RSI超卖再考虑反弹。"
    elif cp <= rl * 1.03:
        return "🟢 潜伏期（底部区域）", "价格在主力洗盘底线附近摩擦，等放量突破。"
    elif ma10 and cp < ma10:
        return "🚨 衰竭期（跌破生命线）", f"价格跌破MA10({ma10:.4f})，趋势走弱信号。"
    elif cp >= rh * 0.97:
        return "⚡ 冲顶期（高位博弈）", f"逼近近期高点{rh}，多空博弈白热化。"
    else:
        return "⏳ 震荡期（蓄势待发）", "行情震荡，等方向选择。"

# ================= 参谋长解读 =================
def generate_commander_comment(r, is_triggered):
    md = r.get("market_data", {})
    sr_list = r.get("strategy_results", {})
    rsi = md.get("rsi")
    adx = md.get("adx")
    fp = md.get("funding_percentile")
    fr = md.get("funding_rate")
    is_spot_mode = md.get("data_mode") == "spot"
    trend_1d = md.get("trend_1d")
    trend_4h = md.get("trend_4h")
    rsi_1h = md.get("rsi_1h")

    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    adx_str = f"{adx:.1f}" if adx is not None else "N/A"
    fp_str = f"{fp:.1%}" if fp is not None else "N/A"
    fr_str = f"{fr:.4f}%" if fr is not None else "N/A"

    best_sr, max_score = None, -1
    for sname, sr in sr_list.items():
        if sr:
            for tk in ["track_1", "track_2", "track_3", "track_4"]:
                s = sr.get(tk, {}).get("score", 0)
                if s > max_score: max_score, best_sr = s, sr

    comment = ""
    if is_triggered and best_sr:
        direction = best_sr.get("direction")
        if direction == "short":
            sub_type = best_sr.get("track_2", {}).get("sub_type", "reversal")
            if sub_type == "trend_follow":
                comment = f"兄弟们，{r['symbol']} 双周期空头共振（1D={trend_1d}，4H={trend_4h}）。价格跌破生命线，正在下跌中继。这种反弹就是给空头送钱的机会，顺势追空，带好2%止损！"
            else:
                comment = f"兄弟们，{r['symbol']} 主力磨刀霍霍了！RSI飙到{rsi_str}，费率{fr_str}，多头拥挤度{fp_str}。1D和4H共振向下，这是主力准备'一锅端'的信号！"
        elif direction == "spot_warning":
            comment = f"警报！{r['symbol']} 现货模式出现逃顶信号！RSI={rsi_str}，价格逼近前高。现货不可做空，建议逐步止盈减仓！"
        elif direction == "long_pullback":
            comment = f"黄金坑！{r['symbol']} 1D+4H双多头共振，价格回踩至1H布林中轨附近。这种趋势中的回踩是难得的加仓机会，缩量止跌就是入场信号！"
        elif direction == "long_rebound":
            comment = f"绝地反击！{r['symbol']} RSI砸到{rsi_str}，1H RSI={rsi_1h if rsi_1h else 'N/A'}，CVD底背离暴露了主力吸筹阴谋。带血的筹码，参谋长笑纳了！"
        elif direction == "long_trend":
            comment = f"{r['symbol']} 蓄力完毕，主力点火起飞！双周期共振多头，动能温和。这波趋势我们要吃满！"
        return f"<div style='background:#fff3cd; padding:12px; border-left:5px solid #e74c3c; margin-top:10px; border-radius:5px;'><b>🐮 参谋长解读：</b><span style='color:#c0392b; font-weight:bold;'>{comment}</span></div>"

    if best_sr:
        if adx is not None and adx < 20:
            comment = f"{r['symbol']} 现在ADX只有{adx_str}，主力高度控盘，无序震荡，进去就是送人头。管住手！"
        elif rsi is not None and rsi >= 70 and not is_spot_mode:
            comment = f"注意风险！{r['symbol']} 30m RSI={rsi_str}，当前费率{fr_str}。别被FOMO冲昏头脑，等它见顶信号，准备反手做空！"
        elif rsi is not None and rsi <= 35:
            comment = f"机会在酝酿！{r['symbol']} RSI={rsi_str}，极度恐慌。子弹已经上膛，等企稳信号！"
        else:
            if max_score >= 3: comment = f"马上要触发了！{r['symbol']} 各项指标都在临界点，主力意图已经暴露，死死盯盘！"
            elif max_score >= 2: comment = f"{r['symbol']} 盘面暗流涌动，主力小动作藏不住了。耐心等信号。"
            else: comment = f"{r['symbol']} 目前垃圾时间，各项指标不达标。空仓休息，等信号。"
    return f"<div style='background:#f8f9fa; padding:12px; border-left:5px solid #3498db; margin-top:10px; border-radius:5px;'><b>🐮 参谋长解读：</b><span style='color:#2c3e50;'>{comment}</span></div>"

def render_track_details(details, max_score):
    """渲染轨道details（含分隔符）"""
    html = ""
    for k, v in details.items():
        if v == "" or v is None:
            if k.startswith("────────"):
                title = k.replace("────────", "").strip()
                html += f"<p style='margin:10px 0 5px 0; font-weight:bold; color:#2c3e50; background:#f0f0f0; padding:5px 8px; border-radius:4px; font-size:13px;'>{title}</p>"
            continue
        exp = CONDITION_EXPLANATIONS.get(k, "")
        if k.startswith("📊"):
            html += f"<li style='list-style:none; margin-left:-15px; color:#e67e22; font-weight:bold;'><b>{k}:</b> {v}</li>"
        else:
            html += f"<li style='margin-bottom:5px;'><b>{k}:</b> {v}"
            if exp: html += f" <span style='color:#888;'>({exp})</span>"
            html += "</li>"
    return html

# ================= 标的独立卡片 =================
def build_symbol_block(r):
    md = r.get("market_data", {})
    cp = r.get("current_price")
    cp_str = f"${cp:.4f}" if isinstance(cp, (int, float)) else "N/A"
    is_spot_mode = md.get("data_mode") == "spot"

    triggered_direction = None
    active_sr = None
    for sname, sr_obj in r.get("strategy_results", {}).items():
        if sr_obj and sr_obj.get("triggered"):
            triggered_direction = sr_obj.get("direction")
            active_sr = sr_obj
            break

    is_triggered = triggered_direction is not None

    if is_triggered:
        if triggered_direction == "spot_warning":
            border_color = "#e67e22"
            header_bg = "linear-gradient(135deg, #e67e22 0%, #d35400 100%)"
            badge_text = "🟢 现货逃顶"
            badge_bg = "#f39c12"; badge_color = "#ffffff"
            track_name = "🟢 现货逃顶预警（建议减仓）"
            track_key = "track_2"
        elif triggered_direction == "short":
            sub_type = active_sr.get("track_2", {}).get("sub_type", "reversal")
            if sub_type == "trend_follow":
                track_name = "📉 顺势做空（趋势延续）"
                border_color = "#8e44ad"
                header_bg = "linear-gradient(135deg, #8e44ad 0%, #6c3483 100%)"
            else:
                track_name = "🔪 见顶做空（反转）"
                border_color = "#d32f2f"
                header_bg = "linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%)"
            track_key = "track_2"
            badge_text = "🚨 开枪信号"
            badge_bg = "#ffc107"; badge_color = "#b71c1c"
        elif triggered_direction == "long_pullback":
            track_name = "🎯 趋势回踩做多"
            border_color = "#27ae60"
            header_bg = "linear-gradient(135deg, #27ae60 0%, #1e8449 100%)"
            track_key = "track_4"
            badge_text = "🚨 开枪信号"
            badge_bg = "#ffc107"; badge_color = "#b71c1c"
        else:
            track_key = {"long_trend":"track_1","long_rebound":"track_3"}.get(triggered_direction,"track_1")
            track_name = {"long_trend":"🚀 底部突破做多","long_rebound":"🩸 暴跌反弹做多"}.get(triggered_direction,"未知")
            border_color = "#d32f2f"
            header_bg = "linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%)"
            badge_text = "🚨 开枪信号"
            badge_bg = "#ffc107"; badge_color = "#b71c1c"
    else:
        border_color = "#34495e"
        header_bg = "linear-gradient(135deg, #2c3e50 0%, #1a252f 100%)"
        badge_text = "🔮 盘面推演"
        badge_bg = "#95a5a6"; badge_color = "#ffffff"

    data_source = md.get('data_source') or '未知数据源'
    funding_pct = md.get('funding_percentile')
    funding_rate = md.get('funding_rate')

    if is_spot_mode:
        header_info = f"<div><b>当前价格：</b><span style='color: {border_color}; font-size: 1.2em; font-weight: bold;'>{cp_str}</span></div><div><b>模式：</b>现货分析 ⚠️</div><div><b>资金费率：</b>现货无此数据</div>"
    else:
        fp_str = f"{funding_pct:.1%}" if funding_pct is not None else "N/A"
        fr_str = f"{funding_rate:.4f}%" if funding_rate is not None else "N/A"
        header_info = f"<div><b>当前价格：</b><span style='color: {border_color}; font-size: 1.2em; font-weight: bold;'>{cp_str}</span></div><div><b>数据源：</b>{data_source}</div><div><b>费率：</b>{fr_str}（拥挤度{fp_str}）</div>"

    stage_title, stage_desc = generate_market_stage(r)
    stage_bg = "#fff3cd" if is_triggered else "#f0f8ff"
    stage_border = "#e74c3c" if is_triggered else "#3498db"

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
            {header_info}
        </div>

        <div style="margin: 15px; padding: 12px; background: {stage_bg}; border-left: 5px solid {stage_border}; border-radius: 0 8px 8px 0;">
            <p style="margin: 0; font-size: 15px; font-weight: bold; color: {stage_border};">🧭 参谋长行情阶段：{stage_title}</p>
            <p style="margin: 5px 0 0 0; font-size: 13px; color: #555; line-height: 1.5;">{stage_desc}</p>
        </div>
    """

    # 🚀 多周期面板
    html += f"<div style='padding: 0 20px;'>{render_multi_tf_panel(md)}</div>"

    if is_triggered:
        ta = active_sr.get(track_key, {})
        plan = calc_trade_plan(triggered_direction, cp, md.get("atr"), md.get("recent_high"), md.get("recent_low"))
        direction_label = "逃顶减仓" if triggered_direction == "spot_warning" else ("做多" if triggered_direction.startswith("long") else "做空")
        max_score = ta.get("max", 6)

        html += f"<div style='padding: 20px;'>"
        html += f"<h3 style='color: {border_color}; margin-top:0; font-size: 20px;'>✅ {track_name}（得分 {ta.get('score',0)}/{max_score}）</h3>"
        html += f"<p style='background:#fff0f0; padding:10px; border-radius:5px;'><b>参谋长理由：</b>{active_sr.get('reason','')}</p>"
        html += "<p style='font-weight:bold; border-bottom: 2px dashed #eee; padding-bottom:5px;'>🔍 狙击细节：</p><ul style='font-size: 14px; color: #444;'>"
        html += render_track_details(ta.get("details", {}), max_score)
        html += "</ul>"

        html += f"<div style='background:#fffbea; padding: 15px; border-left: 5px solid #f39c12; margin-top: 15px; border-radius: 0 8px 8px 0;'>"
        html += f"<h4 style='margin-top:0; color:#e67e22; font-size: 18px;'>🎯 参谋长具体操作指令</h4>"
        html += f"<p><b>方向：</b>{direction_label} {r['symbol'].replace('_USDT','')} | <b>入场价：</b>{cp_str}</p>"
        if plan["stop"]:
            html += f"<p><b>2%资金管理（本金10000U）：</b>止损空间 {plan['risk_pct']*100:.2f}%，最大总仓位 <b>{plan['position_value']:.2f} USDT</b>。<br>10倍杠杆下，投入保证金 <b>{plan['margin_10x']:.2f} USDT</b>，开仓数量 <b>{plan['coin_amount']:.4f} 个</b>。</p>"
            html += f"<p><b>硬止损价：</b><span style='color:#d32f2f; font-weight:bold;'>${plan['stop']:.4f}</span></p>"
        if plan["tp1_trigger"]:
            html += f"<p><b>📐 三段式移动止盈：</b><br>"
            html += f"1️⃣ 价格到 <b>${plan['tp1_trigger']:.4f}</b> 时，止损移至成本价 ${plan['tp1_stop']:.4f}<br>"
            html += f"2️⃣ 价格到 <b>${plan['tp2_trigger']:.4f}</b> 时，止损移至 ${plan['tp2_stop']:.4f}<br>"
            html += f"3️⃣ 价格到 <b>${plan['tp3_trigger']:.4f}</b> 时，止损移至 ${plan['tp3_stop']:.4f}，止盈50%仓位</p>"
        ma10_val = md.get('ma10')
        ma10_str = f"${ma10_val:.4f}" if isinstance(ma10_val, (int, float)) else "N/A"
        html += f"<p><b>MA10动态离场线：</b>{ma10_str}</p>"
        html += "</div>"
        html += generate_commander_comment(r, is_triggered=True)
        html += "</div>"
    else:
        html += f"<div style='padding: 20px;'>"
        html += f"<p style='color:#666; font-size:15px;'>当前标的尚未触发开枪信号，以下是各轨道的推演情况：</p>"
        for sname, sr_obj in r.get("strategy_results", {}).items():
            if not sr_obj: continue
            html += f"<div style='margin-top:15px; padding-left:10px; border-left:4px solid #8e44ad; background:#fafafa; padding:10px; border-radius:0 5px 5px 0;'>"
            html += f"<p style='margin-top:0; font-weight:bold; color:#8e44ad;'>策略推演：{sname}</p>"

            track2_name = "🟢 现货逃顶预警" if is_spot_mode else "🔪 见顶做空 + 📉 顺势做空"
            for tk, tn in [("track_1","🚀 底部突破做多"),("track_2",track2_name),("track_3","🩸 暴跌反弹做多"),("track_4","🎯 趋势回踩做多")]:
                ta = sr_obj.get(tk, {})
                hard = "✅ 硬条件通过" if ta.get("hard_ok") else "❌ 硬条件未过"
                score = ta.get('score', 0)
                max_score = ta.get('max', 6)
                html += f"<p style='margin:5px 0;'><b>{tn}：{score}/{max_score} {hard}</b></p>"
                details = ta.get("details", {})
                if details:
                    html += "<ul style='color:#555; font-size:0.85em; margin-top:2px; margin-bottom:10px;'>"
                    html += render_track_details(details, max_score)
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
    html += "<h3 style='margin-top:0; color:#2c3e50; border-bottom:2px dashed #eee; padding-bottom:10px;'>📋 本期监控全景图</h3>"
    if triggered_syms:
        badges = "".join([f"<span style='background:#d32f2f;color:white;padding:3px 8px;border-radius:5px;font-weight:bold;margin:2px;'>{s}</span>" for s in triggered_syms])
        html += f"<p style='font-size: 16px; color: #e74c3c;'><b>🚨 触发信号：</b> {badges}</p>"
    else:
        html += f"<p style='font-size: 16px; color: #888;'><b>🚨 触发信号：</b> 无。</p>"
    if untriggered_syms:
        badges = "".join([f"<span style='background:#ecf0f1;color:#2c3e50;padding:3px 8px;border-radius:5px;margin:2px;'>{s}</span>" for s in untriggered_syms])
        html += f"<p style='font-size: 14px; color: #555;'><b>🔮 盘面推演：</b> {badges}</p>"
    html += "</div>"
    return html

def generate_dynamic_subject(triggered_list, untriggered_list, results):
    tr_syms = [r['symbol'].replace('_USDT','') for r in triggered_list]
    all_directions, all_sub = [], []
    max_rsi, min_rsi, max_fp, min_adx = 0, 100, 0, 100

    for r in results:
        if r.get("status") != "ok": continue
        md = r.get("market_data", {})
        rsi, adx, fp = md.get("rsi"), md.get("adx"), md.get("funding_percentile")
        if rsi: max_rsi, min_rsi = max(max_rsi, rsi), min(min_rsi, rsi)
        if adx: min_adx = min(min_adx, adx)
        if fp: max_fp = max(max_fp, fp)
        for sr in r.get("strategy_results", {}).values():
            if sr and sr.get("triggered"):
                all_directions.append(sr.get("direction"))
                if sr.get("direction") == "short":
                    all_sub.append(sr.get("track_2", {}).get("sub_type"))

    is_short = "short" in all_directions
    is_long = any(d.startswith("long") for d in all_directions)
    has_tf = "trend_follow" in all_sub

    if triggered_list:
        if len(triggered_list) >= 2:
            return random.choice([
                f"🚨【参谋长重磅战报】多空双杀！{', '.join(tr_syms[:3])}全线暴动！速看！",
                f"🔥【牛来参谋长】冰火两重天！多币种触发信号，主力底牌已被看穿！",
                f"💥【参谋长战报】大行情来了！{', '.join(tr_syms[:3])}齐爆，跟紧不迷路！"
            ])
        elif is_short and is_long:
            return f"🚨【参谋长战报】多空双杀！主力露出獠牙！"
        elif is_short:
            if has_tf:
                return random.choice([
                    f"📉【参谋长战报】下跌中继确认！{tr_syms[0]}顺势做空机会已到！",
                    f"🔪【牛来参谋长】错过顶部不要紧，{tr_syms[0]}顺势空单照样吃肉！"
                ])
            return f"🩸【参谋长战报】瀑布警告！{tr_syms[0]}即将暴跌？"
        else:
            return f"🚀【参谋长战报】火箭点火！{tr_syms[0]}暴力拉升！"
    else:
        if min_adx < 20:
            return random.choice([f"⚠️【参谋长推演】大盘死水微澜？主力正在密谋大动作！", f"🛡️【参谋长推演】ADX告急！主力高度控盘！"])
        elif max_rsi >= 70:
            return random.choice([f"🎈【参谋长预警】极度贪婪！RSI飙至{max_rsi:.0f}！", f"🔪【参谋长推演】散户狂欢倒计时？"])
        elif min_rsi <= 35:
            return random.choice([f"🩸【参谋长推演】极度恐慌！RSI砸至{min_rsi:.0f}！", f"💎【参谋长推演】带血的筹码满地都是！"])
        else:
            return random.choice([f"🔮【参谋长推演】多空博弈白热化！", f"🐮【参谋长推演】盘面暗流涌动！"])

def build_report(results, active_strategies, watchlist):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    triggered_list, untriggered_list = [], []
    for r in results:
        has = any(sr and sr.get("triggered") for sr in r.get("strategy_results", {}).values())
        (triggered_list if has else untriggered_list).append(r)

    subject = generate_dynamic_subject(triggered_list, untriggered_list, results)

    html = f"<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#333;padding:10px; background-color:#f4f6f8;'>"
    html += AD_BANNER
    html += f"<div style='background:white; padding:20px; border-radius:10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);'>"
    html += f"<h2 style='border-bottom: 3px solid #e74c3c; padding-bottom: 10px; color:#2c3e50;'>📊 参谋长多周期共振报告</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} | <b>策略：</b>{', '.join(active_strategies)} | <b>周期：</b>1D · 4H · 1H · 30m</p>"

    html += build_dashboard(results, triggered_list, untriggered_list)

    if triggered_list:
        html += "<h3 style='color:#e74c3c; border-left:5px solid #e74c3c; padding-left:10px; font-size:22px; margin-top:30px;'>🚨 参谋长开枪警告</h3>"
        for r in triggered_list: html += build_symbol_block(r)

    if untriggered_list:
        html += "<hr><h3 style='color:#27ae60; border-left:5px solid #27ae60; padding-left:10px; font-size:20px; margin-top:30px;'>🔮 盘面推演（未触发）</h3>"
        for r in untriggered_list: html += build_symbol_block(r)

    html += build_unsupported_section(results)
    html += build_glossary_section()
    html += build_risk_warning()
    html += f"<p style='text-align:center; color:#e67e22; font-weight:bold; font-size:15px; margin-top:20px;'>👉 点赞、转发、关注“牛来参谋长”！</p>"
    html += "</div></body></html>"
    return subject, html

def build_unsupported_section(results):
    unsupported = [r for r in results if r.get("status") == "unsupported"]
    if not unsupported: return ""
    html = "<hr><h3 style='color:#7f8c8d;'>⚠️ 暂不支持的标的</h3><ul>"
    for r in unsupported: html += f"<li>{r['symbol']}：合约和现货均无数据。</li>"
    html += "</ul>"
    return html

def build_glossary_section():
    html = "<hr><h3 style='color:#34495e; border-left:5px solid #34495e; padding-left:10px;'>📖 参谋长指标词典</h3>"
    html += "<div style='background:#f8f9fa; padding:15px; border-radius:8px; font-size:14px; color:#555;'>"
    for name, desc in SIGNAL_GLOSSARY.items():
        html += f"<p style='margin: 8px 0;'><b>• {name}：</b>{desc}</p>"
    html += "</div>"
    return html

def build_risk_warning():
    return """
<div style="background: linear-gradient(135deg, #1a1a1a 0%, #2d1b1b 100%); color: #e0e0e0; padding: 25px; border-radius: 12px; margin-top: 25px; border: 1px solid #4a2c2c; box-shadow: 0 4px 15px rgba(0,0,0,0.3);">
    <h3 style="margin: 0 0 15px 0; color: #ffc107; font-size: 20px; text-align: center; letter-spacing: 2px;">⚠️ 参谋长最后说句掏心窝子的话</h3>
    <p style="font-size: 15px; line-height: 1.8; margin: 0 0 10px 0;">合约市场是个绞肉机。<b style="color: #ffc107;">90%的人死在这里，不是因为他们不够聪明，而是因为他们管不住手、舍不得止损、扛不住单。</b></p>
    <p style="font-size: 15px; line-height: 1.8; margin: 0 0 15px 0;"><b style="color: #ffc107;">参谋长给你的不是暴富密码，是一把刀。</b>刀怎么用，能不能活着走出来，看你自己。</p>
    <div style="background: rgba(211, 47, 47, 0.2); padding: 12px; border-radius: 8px; text-align: center;">
        <p style="font-size: 16px; line-height: 1.8; margin: 0; color: #ff5252; font-weight: bold;">记住：止损是你唯一的朋友。仓位是你唯一的铠甲。</p>
    </div>
    <p style="font-size: 12px; line-height: 1.6; margin: 20px 0 0 0; color: #888; text-align: center;">本报告由牛来参谋长自研系统生成，仅供交流参考，不构成投资建议。<br>加密货币交易具有极高风险，可能导致全部本金损失。</p>
</div>
"""
