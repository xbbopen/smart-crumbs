# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta
import os
import json
import random

from report_lexicon import (
    TREND_MAP, MOMENTUM_4H_MAP, RSI_DIV_MAP, REGIME_MAP,
    SUB_TYPE_MAP, DATA_MODE_MAP, TRACK_NAME_MAP, SESSION_MAP,
    SUBJECT_POOL, COMMENT_POOL, HOTSPOT_POOL,
    fill, pick_unique, pick_one,
    translate_track_ids, build_smart_tips,
)

BJT = timezone(timedelta(hours=8))

HISTORY_FILE = "logs/subject_history.json"
HISTORY_SIZE = 20


# ============================================================
# 一、翻译工具
# ============================================================
def translate_asset_type(asset_type):
    return DATA_MODE_MAP.get(asset_type, asset_type or "未知")


def translate_trend(t):
    return TREND_MAP.get(t, "❓ 未知")


def _fmt_num(v, fmt=".1f", default="N/A"):
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return format(f, fmt)
    except (TypeError, ValueError):
        return default


# ============================================================
# 二、标题去重
# ============================================================
def _load_subject_history():
    try:
        if not os.path.exists(HISTORY_FILE):
            return []
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_subject_history(history):
    try:
        os.makedirs("logs", exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-HISTORY_SIZE:], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _pick_unique_subject(pool):
    """从标题池里随机抽取，尽量不与历史重复。最多重试 8 次。"""
    history = _load_subject_history()
    for _ in range(8):
        candidate = random.choice(pool)
        if candidate not in history:
            history.append(candidate)
            _save_subject_history(history)
            return candidate
    candidate = random.choice(pool)
    history.append(candidate)
    _save_subject_history(history)
    return candidate


# ============================================================
# 三、常量
# ============================================================
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
    "A4.1H RSI超买 / 顶背离 / 费率极端": "1H是否极度狂热、顶背离或费率拥挤",
    "A5.1H KDJ超买": "1H短期是否过热",
    "A6.4H MACD死叉": "4H动量是否转空",
    "B1.4H空头结构": "4H EMA20 < EMA50，中期空头结构",
    "B2.价格接近/高于布林中轨": "等反弹到1H布林中轨附近，是顺势做空的最佳位置",
    "B3.1H RSI中位": "1H RSI在40-60，说明跌势未完成",
    "B4.30m反弹遇阻": "反弹时出现长上影或看跌吞没",
    "B5.1H MACD空头": "1H动量偏空",
    "B6.30m RSI未超买": "30m RSI<60，没有极度狂热",
    "1.大幅回撤": "是否跌出黄金坑",
    "2.30m RSI超卖": "30m是否极度恐慌",
    "3.30m CVD牛背离": "价跌但主力暗中吸筹",
    "4.1H RSI超卖或底背离": "1H是否极度恐慌或出现底背离",
    "5.1H KDJ超卖": "1H抛压耗尽",
    "6.30m止跌形态": "出现锤头线或看涨吞没",
    "1.回踩1H布林中轨": "价格回踩到1H布林中轨附近（动态 ATR 容差）",
    "2.缩量回踩": "回踩时成交量萎缩（卖压衰竭）",
    "3.30m止跌形态": "出现止跌K线",
    "4.1H RSI健康": "1H RSI在40-55之间",
    "5.站上30m MA10": "短期生命线收复",
    "6.4H MACD健康": "4H MACD未死叉",
}

SIGNAL_GLOSSARY = {
    "多周期共振": "1D定大方向，4H定中期趋势，1H找入场时机，30m精确扣扳机。",
    "分档入场": "根据不同轨道的性质，把仓位拆成2-3档，避免一次性满仓。",
    "ADX": "趋势强度。轨道1要求≥22，轨道2要求≥20，轨道4要求≥18，轨道3放宽到≥12。",
    "CVD": "累积成交量差。价格新高但CVD未新高=买盘衰竭（顶背离）。",
    "RSI背离": "价格新高但RSI未新高=顶背离；价格新低但RSI未新低=底背离。",
    "资金费率": "Hyperliquid每小时结算，正常0.001%-0.003%，0.005%以上警告，0.01%以上极端。",
    "KDJ": "J值<0超卖，>100超买，适合捕捉短期极端。",
    "布林中轨": "1H的MA20，趋势回踩的经典支撑位。",
    "ATR": "波动幅度，用于止损和仓位计算。",
    "动量停滞": "MACD柱归零 + ADX低位，主力按兵不动，多数轨道不宜出手。",
}

AD_BANNER = """
<div style="text-align: center; margin-bottom: 20px;">
    <img src="https://raw.githubusercontent.com/xbbopen/smart-crumbs/main/banner.png" 
         alt="牛来参谋长" loading="lazy" decoding="async"
         style="width: 100%; max-width: 800px; height: auto; border-radius: 12px; display: block; margin: 0 auto; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
</div>
"""


# ============================================================
# 四、多周期面板
# ============================================================
def _fmt_data_range(klines, period_label):
    """格式化 K 线区间：N根（起始 ~ 结束），时间为北京时间。"""
    if not klines:
        return f"{period_label} N/A"
    try:
        start_ts = klines[0]["timestamp"] / 1000
        end_ts = klines[-1]["timestamp"] / 1000
        start_str = datetime.fromtimestamp(start_ts, BJT).strftime("%m-%d %H:%M")
        end_str = datetime.fromtimestamp(end_ts, BJT).strftime("%m-%d %H:%M")
        return f"{period_label} {len(klines)}根（{start_str} ~ {end_str}）"
    except Exception:
        return f"{period_label} {len(klines)}根"


def render_multi_tf_panel(md, regime=None):
    """
    - 传入 regime 让面板提示与参谋长解读一致。
    - 表格下方显示"数据区间"，明确各周期实际使用的 K 线范围。
    """
    trend_1d = md.get("trend_1d")
    trend_4h = md.get("trend_4h")
    rsi_1h = md.get("rsi_1h")
    rsi_4h = md.get("rsi_4h")
    rsi_1d = md.get("rsi_1d")
    kdj_1h = md.get("kdj_1h") or {}
    boll_1h = md.get("boll_1h") or {}
    macd_4h = md.get("macd_4h") or {}
    ema50_1d = md.get("ema50_1d")
    ema20_4h = md.get("ema20_4h")
    ema50_4h = md.get("ema50_4h")
    price = md.get("current_price")
    rsi_div = md.get("rsi_div_1h")
    rsi_30m = md.get("rsi")
    ma10_30m = md.get("ma10")
    adx_30m = md.get("adx")

    rsi_1d_str = _fmt_num(rsi_1d)
    rsi_4h_str = _fmt_num(rsi_4h)
    rsi_1h_str = _fmt_num(rsi_1h)
    rsi_30m_str = _fmt_num(rsi_30m)

    h1_trend = "neutral"
    if price and boll_1h.get("mid"):
        if price > boll_1h["mid"] * 1.005: h1_trend = "up"
        elif price < boll_1h["mid"] * 0.995: h1_trend = "down"

    m30_trend = "neutral"
    if price and ma10_30m:
        if price > ma10_30m * 1.005: m30_trend = "up"
        elif price < ma10_30m * 0.995: m30_trend = "down"

    day_signal = "N/A"
    if ema50_1d:
        pos = "上方" if (price and price > ema50_1d) else "下方"
        day_signal = f"EMA50={ema50_1d:.4f} | 价格{pos}"

    h4_signal_list = []
    if ema20_4h: h4_signal_list.append(f"EMA20={ema20_4h:.4f}")
    if ema50_4h: h4_signal_list.append(f"EMA50={ema50_4h:.4f}")
    if macd_4h.get("hist") is not None:
        h4_signal_list.append(f"MACD柱={'正' if macd_4h['hist'] > 0 else '负'}")
    h4_signal = " | ".join(h4_signal_list) if h4_signal_list else "N/A"

    h1_signal_list = []
    if boll_1h.get("mid"): h1_signal_list.append(f"BOLL中轨={boll_1h['mid']:.4f}")
    if kdj_1h.get("j") is not None: h1_signal_list.append(f"KDJ J={kdj_1h['j']:.1f}")
    if rsi_div:
        # 兜底翻译：未知值 → "无背离"，避免英文暴露
        h1_signal_list.append(f"RSI背离={RSI_DIV_MAP.get(rsi_div, '无背离')}")
    h1_signal = " | ".join(h1_signal_list) if h1_signal_list else "N/A"

    m30_signal_list = []
    if ma10_30m: m30_signal_list.append(f"MA10={ma10_30m:.4f}")
    if adx_30m is not None: m30_signal_list.append(f"ADX={adx_30m:.1f}")
    m30_signal = " | ".join(m30_signal_list) if m30_signal_list else "N/A"

    # 数据区间
    klines_30m = md.get("klines_30m") or []
    klines_1h = md.get("klines_1h") or []
    klines_4h = md.get("klines_4h") or []
    klines_1d = md.get("klines_1d") or []
    range_30m = _fmt_data_range(klines_30m, "30m")
    range_1h = _fmt_data_range(klines_1h, "1H")
    range_4h = _fmt_data_range(klines_4h, "4H")
    range_1d = _fmt_data_range(klines_1d, "1D")

    html = "<div style='background:#f8f9fa; padding:15px; border-radius:8px; margin-top:15px; border:1px solid #eee;'>"
    html += "<h4 style='margin:0 0 12px 0; color:#2c3e50;'>🌐 多周期共振面板</h4>"
    html += "<table style='width:100%; font-size:13px; border-collapse:collapse;'>"
    html += "<tr style='background:#e8e8e8;'><th style='padding:6px; text-align:left;'>周期</th><th style='padding:6px;'>趋势</th><th style='padding:6px;'>RSI</th><th style='padding:6px;'>关键位/信号</th></tr>"
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>📅 日线</td><td style='text-align:center;'>{translate_trend(trend_1d)}</td><td style='text-align:center;'>{rsi_1d_str}</td><td style='text-align:center; font-size:12px;'>{day_signal}</td></tr>"
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>⏰ 4小时</td><td style='text-align:center;'>{translate_trend(trend_4h)}</td><td style='text-align:center;'>{rsi_4h_str}</td><td style='text-align:center; font-size:12px;'>{h4_signal}</td></tr>"
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>🕐 1小时</td><td style='text-align:center;'>{translate_trend(h1_trend)}</td><td style='text-align:center;'>{rsi_1h_str}</td><td style='text-align:center; font-size:12px;'>{h1_signal}</td></tr>"
    html += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:6px; font-weight:bold;'>⏱️ 30分钟</td><td style='text-align:center;'>{translate_trend(m30_trend)}</td><td style='text-align:center;'>{rsi_30m_str}</td><td style='text-align:center; font-size:12px;'>{m30_signal}</td></tr>"
    html += "</table>"

    # 数据区间行
    html += (
        "<div style='margin-top:10px; padding:8px 10px; background:#fff; "
        "border-left:3px solid #95a5a6; border-radius:0 4px 4px 0; font-size:11.5px; color:#666;'>"
        f"<b>📊 数据区间：</b>{range_30m} ｜ {range_1h} ｜ {range_4h} ｜ {range_1d}"
        "</div>"
    )

    # tips 基于 regime（与参谋长解读一致）
    try:
        if regime:
            tips = build_smart_tips(md, regime)
        else:
            tips = []
            if trend_1d == "up": tips.append("日线多头")
            elif trend_1d == "down": tips.append("日线空头")
            else: tips.append("日线震荡")
        tip_text = "；".join(tips) if tips else "观望"
    except Exception:
        tip_text = "观望"

    html += (
        "<div style='margin-top:12px; padding:10px; background:#fff; "
        "border-left:4px solid #3498db; border-radius:0 5px 5px 0;'>"
        f"<p style='margin:0; font-size:13px; color:#2c3e50;'>"
        f"<b>💡 参谋长多周期提示：</b>{tip_text}。</p></div>"
    )
    html += "</div>"
    return html


# ============================================================
# 五、行情阶段推演
# ============================================================
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

    try:
        if trend_1d == "up" and trend_4h == "up":
            if ma10 and cp < ma10 and cp > rl * 1.05:
                return "🔥 多头回踩期", "1D+4H双多头共振，价格回踩至生命线下方。这是经典的'趋势回踩'黄金坑，等30m止跌即可入场。"
            else:
                return "🚀 多头趋势期", "1D+4H双多头共振，趋势健康。回调即买入机会，坚决不做空。"
        elif trend_1d == "down" and trend_4h == "down":
            return "🔪 空头趋势期", "1D+4H双空头共振，反弹即做空机会。绝对不要抄底，等暴跌后RSI超卖再考虑反弹。"
        elif cp <= rl * 1.03:
            return "🟢 潜伏期（底部区域）", "价格在主力洗盘底线附近摩擦，等放量突破。"
        elif ma10 and cp < ma10:
            return "🚨 衰竭期（跌破生命线）", f"价格跌破MA10({ma10:.4f})，趋势走弱信号。"
        elif cp >= rh * 0.97:
            return "⚡ 冲顶期（高位博弈）", f"逼近近期高点{rh}，多空博弈白热化。"
        else:
            return "⏳ 震荡期（蓄势待发）", "行情震荡，等方向选择。"
    except Exception as e:
        return "推演异常", f"阶段推演出错：{type(e).__name__}: {e}"


# ============================================================
# 六、参谋长解读（话术池 + 数据锚点）
# ============================================================
def generate_commander_comment(r, is_triggered):
    md = r.get("market_data", {})
    sr_list = r.get("strategy_results", {})

    sym_raw = r.get("symbol", "")
    sym = sym_raw.replace("_USDT", "")
    cp = r.get("current_price")
    rsi = md.get("rsi")
    rsi_1h = md.get("rsi_1h")
    adx = md.get("adx")
    fr = md.get("funding_rate")
    fp = md.get("funding_percentile")
    rh = md.get("recent_high")
    rl = md.get("recent_low")
    ma10 = md.get("ma10")
    kdj_1h = md.get("kdj_1h") or {}
    kdj_j = kdj_1h.get("j")
    boll_1h = md.get("boll_1h") or {}
    mid = boll_1h.get("mid")
    is_spot_mode = md.get("data_mode") == "spot"

    drawdown = 0.0
    if cp and rh and rh > 0:
        drawdown = (rh - cp) / rh * 100

    best_sr, max_score = None, -1
    for sname, sr in sr_list.items():
        if sr:
            for tk in ["track_1", "track_2", "track_3", "track_4"]:
                s = sr.get(tk, {}).get("score", 0)
                if s > max_score:
                    max_score, best_sr = s, sr

    if best_sr and best_sr.get("error"):
        return (
            "<div style='background:#ffebee; padding:12px; border-left:5px solid #d32f2f; "
            "margin-top:10px; border-radius:5px;'>"
            f"<b>🐮 参谋长解读：</b><span style='color:#c62828; font-weight:bold;'>"
            f"⚠️ 该标的策略执行异常，本轮无法推演。错误：{best_sr['error']}</span></div>"
        )
    if best_sr is None:
        return (
            "<div style='background:#fff3e0; padding:12px; border-left:5px solid #ef6c00; "
            "margin-top:10px; border-radius:5px;'>"
            f"<b>🐮 参谋长解读：</b><span style='color:#e65100;'>"
            f"该标的未返回任何策略结果，请检查 monitor 日志。</span></div>"
        )

    fill_kwargs = {
        "sym": sym,
        "rsi": _fmt_num(rsi),
        "rsi_1h": _fmt_num(rsi_1h),
        "adx": _fmt_num(adx),
        "fr": _fmt_num(fr, ".4f"),
        "fp": f"{fp:.1%}" if fp is not None else "N/A",
        "rh": _fmt_num(rh, ".4f"),
        "rl": _fmt_num(rl, ".4f"),
        "ma10": _fmt_num(ma10, ".4f"),
        "mid": _fmt_num(mid, ".4f"),
        "kdj_j": _fmt_num(kdj_j),
        "drawdown": _fmt_num(drawdown, ".1f"),
    }

    pool_key = None
    if is_triggered:
        direction = best_sr.get("direction")
        if direction == "short":
            sub_type = best_sr.get("track_2", {}).get("sub_type", "reversal")
            pool_key = "short_trend" if sub_type == "trend_follow" else "short_reversal"
        elif direction == "spot_warning":
            pool_key = "spot_warning"
        elif direction == "long_pullback":
            pool_key = "long_pullback"
        elif direction == "long_rebound":
            pool_key = "long_rebound"
        elif direction == "long_trend":
            pool_key = "long_trend"
    else:
        if rsi is not None and rsi >= 70 and not is_spot_mode:
            pool_key = "no_trigger_greedy"
        elif rsi is not None and rsi <= 35:
            pool_key = "no_trigger_panic"
        else:
            pool_key = "no_trigger_quiet"

    pool = COMMENT_POOL.get(pool_key) or COMMENT_POOL["no_trigger_quiet"]
    template = pick_one(pool)
    comment = fill(template, **fill_kwargs)

    if not comment:
        comment = f"{sym} 盘面暂无明确信号，静观其变。"

    bg = "#fff3cd" if is_triggered else "#f8f9fa"
    border = "#e74c3c" if is_triggered else "#3498db"
    color = "#c0392b" if is_triggered else "#2c3e50"
    return (
        f"<div style='background:{bg}; padding:12px; border-left:5px solid {border}; "
        f"margin-top:10px; border-radius:5px;'>"
        f"<b>🐮 参谋长解读：</b><span style='color:{color};'>{comment}</span></div>"
    )


# ============================================================
# 七、轨道细节渲染
# ============================================================
def render_track_details(details, max_score):
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


# ============================================================
# 八、入场计划渲染
# ============================================================
def render_entry_plan(entry_plan, direction, cp, cp_str, md):
    if not entry_plan:
        return ""

    is_spot_mode = md.get("data_mode") == "spot"
    stages = entry_plan.get("stages", [])
    avg_price = entry_plan.get("avg_price", cp)
    stop = entry_plan.get("stop")
    note = entry_plan.get("note", "")

    direction_label = "做多" if direction and direction.startswith("long") else "做空"
    if direction == "spot_warning": direction_label = "逃顶减仓"

    html = "<div style='background:#fffbea; padding: 15px; border-left: 5px solid #f39c12; margin-top: 15px; border-radius: 0 8px 8px 0;'>"
    html += f"<h4 style='margin-top:0; color:#e67e22; font-size: 18px;'>🎯 参谋长分档{'买入' if is_spot_mode else '入场'}计划</h4>"
    html += f"<p style='font-size:14px; color:#666;'>{note}</p>"
    html += "<table style='width:100%; font-size:13px; border-collapse:collapse; margin-top:8px;'>"
    html += "<tr style='background:#f0f0f0;'><th style='padding:6px;'>档位</th><th style='padding:6px;'>仓位</th><th style='padding:6px;'>类型</th><th style='padding:6px;'>价格</th><th style='padding:6px;'>说明</th></tr>"
    for i, s in enumerate(stages, 1):
        type_label = "市价" if s["type"] == "market" else "限价挂单"
        type_color = "#e74c3c" if s["type"] == "market" else "#3498db"
        html += f"<tr style='border-bottom:1px solid #eee;'>"
        html += f"<td style='padding:6px; text-align:center;'>第{i}档</td>"
        html += f"<td style='padding:6px; text-align:center; font-weight:bold;'>{s['weight']}%</td>"
        html += f"<td style='padding:6px; text-align:center; color:{type_color}; font-weight:bold;'>{type_label}</td>"
        html += f"<td style='padding:6px; text-align:center;'>${s['price']:.4f}</td>"
        html += f"<td style='padding:6px; font-size:12px; color:#666;'>{s['note']}</td></tr>"
    html += "</table>"

    html += f"<p style='margin-top:12px;'><b>加权平均{'买入' if is_spot_mode else '入场'}价：</b><span style='color:#e67e22; font-weight:bold;'>${avg_price:.4f}</span></p>"
    if stop:
        risk_pct = abs(avg_price - stop) / avg_price
        if is_spot_mode:
            max_loss = 200
            position_value = min(10000, max_loss / risk_pct) if risk_pct > 0 else 10000
            coin_amount = position_value / avg_price if avg_price else 0
            html += f"<p><b>止损触发价：</b><span style='color:#d32f2f; font-weight:bold;'>${stop:.4f}</span></p>"
            html += f"<p><b>止损空间：</b>{risk_pct*100:.2f}%</p>"
            html += f"<p><b>🛡️ 现货2%规则（本金10000U）：</b>建议买入 <b>{position_value:.2f} USDT</b>，"
            html += f"对应 <b>{coin_amount:.4f} 个 {md.get('symbol','').replace('_USDT','')}</b>。<br>"
            html += f"<span style='color:#e67e22;'>若止损触发，最大亏损约 {max_loss} U（占本金 2%）。</span></p>"
        else:
            position_pct = min(0.5, 0.02 / risk_pct) if risk_pct > 0 else 0.5
            position_value = 10000 * position_pct
            margin_10x = position_value / 10
            coin_amount = position_value / avg_price if avg_price else 0
            html += f"<p><b>硬止损价：</b><span style='color:#d32f2f; font-weight:bold;'>${stop:.4f}</span></p>"
            html += f"<p><b>止损空间：</b>{risk_pct*100:.2f}%</p>"
            html += f"<p><b>🛡️ 2%资金管理（本金10000U）：</b>最大总仓位 <b>{position_value:.2f} USDT</b>。<br>"
            html += f"10倍杠杆下，投入保证金 <b>{margin_10x:.2f} USDT</b>，"
            html += f"总开仓数量 <b>{coin_amount:.4f} 个</b>（按各档位权重分配）。</p>"

    if avg_price and stop:
        if direction and direction.startswith("long"):
            tp1 = avg_price + abs(avg_price - stop) * 1.0
            tp2 = avg_price + abs(avg_price - stop) * 2.0
            tp3 = avg_price + abs(avg_price - stop) * 3.0
        else:
            tp1 = avg_price - abs(stop - avg_price) * 1.0
            tp2 = avg_price - abs(stop - avg_price) * 2.0
            tp3 = avg_price - abs(stop - avg_price) * 3.0
        html += "<p><b>📐 三段式移动止盈：</b><br>"
        html += f"1️⃣ 价格到 <b>${tp1:.4f}</b> 时，止损移至加权成本价 ${avg_price:.4f}<br>"
        html += f"2️⃣ 价格到 <b>${tp2:.4f}</b> 时，止损移至 ${tp1:.4f}<br>"
        html += f"3️⃣ 价格到 <b>${tp3:.4f}</b> 时，止损移至 ${tp2:.4f}，止盈50%仓位</p>"

    ma10_val = md.get('ma10')
    ma10_str = f"${ma10_val:.4f}" if isinstance(ma10_val, (int, float)) else "N/A"
    html += f"<p><b>MA10动态离场线：</b>{ma10_str}</p>"
    html += "</div>"
    return html


# ============================================================
# 九、错误卡片
# ============================================================
def build_error_card(r, err_msg: str):
    sym = r['symbol'].replace('_USDT', '')
    return f"""
    <div style="border: 3px solid #d32f2f; border-radius: 12px; margin-bottom: 30px; box-shadow: 0 6px 20px rgba(0,0,0,0.15); overflow: hidden;">
        <div style="background: linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%); color: white; padding: 15px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid #ffc107;">
            <div>
                <span style="font-size: 28px; font-weight: 900; letter-spacing: 2px; color: #ffd54f; text-shadow: 1px 1px 3px rgba(0,0,0,0.5);">💥 {sym}</span>
                <span style="font-size: 16px; opacity: 0.9; margin-left: 10px; color: #fff;">策略异常</span>
            </div>
            <div style="background: #ffc107; color: #b71c1c; padding: 6px 15px; border-radius: 20px; font-weight: bold; font-size: 14px;">❌ 未能分析</div>
        </div>
        <div style="padding: 20px; background: #fff;">
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #c62828; font-weight: bold;">❌ 策略执行异常，本轮无法对该标的推演。</p>
            <p style="margin: 0; font-size: 13px; color: #666; font-family: monospace; background: #fafafa; padding: 10px; border-radius: 5px; word-break: break-all;">{err_msg}</p>
            <p style="margin: 12px 0 0 0; font-size: 12px; color: #888;">提示：详见 GitHub Actions 日志中的完整 Python traceback。</p>
        </div>
    </div>
    """


# ============================================================
# 十、热点开场段落（浅色现代风 + 胶囊标签）
# ============================================================
def build_hotspot_section(hotspot):
    if not hotspot:
        return ""
    label = hotspot.get("session_label", "")
    vibe = hotspot.get("session_vibe", "")

    # --- 胶囊标签渲染工具 ---
    def _badge_gainer(items):
        if not items: return "<span style='color:#999;font-size:13px;'>无</span>"
        return "".join([
            f"<span style='display:inline-block;background:#fdeaea;color:#c0392b;padding:3px 10px;border-radius:12px;margin:2px 4px 2px 0;font-size:13px;font-weight:bold;'>{x['sym']} {x['pct']:+.2f}%</span>"
            for x in items
        ])

    def _badge_loser(items):
        if not items: return "<span style='color:#999;font-size:13px;'>无</span>"
        return "".join([
            f"<span style='display:inline-block;background:#eafaf1;color:#27ae60;padding:3px 10px;border-radius:12px;margin:2px 4px 2px 0;font-size:13px;font-weight:bold;'>{x['sym']} {x['pct']:+.2f}%</span>"
            for x in items
        ])

    def _badge_rsi(items, color, bg):
        if not items: return "<span style='color:#999;font-size:13px;'>无</span>"
        return "".join([
            f"<span style='display:inline-block;background:{bg};color:{color};padding:3px 10px;border-radius:12px;margin:2px 4px 2px 0;font-size:13px;font-weight:bold;'>{x['sym']} {x['rsi']:.1f}</span>"
            for x in items
        ])

    def _badge_vol(items):
        if not items: return "<span style='color:#999;font-size:13px;'>无</span>"
        return "".join([
            f"<span style='display:inline-block;background:#f4ecf7;color:#8e44ad;padding:3px 10px;border-radius:12px;margin:2px 4px 2px 0;font-size:13px;font-weight:bold;'>{x['sym']} 爆量 {x['mult']:.1f}x</span>"
            for x in items
        ])

    def _badge_fr(items):
        if not items: return "<span style='color:#999;font-size:13px;'>无</span>"
        return "".join([
            f"<span style='display:inline-block;background:#fef9e7;color:#d35400;padding:3px 10px;border-radius:12px;margin:2px 4px 2px 0;font-size:13px;font-weight:bold;'>{x['sym']} {x['fr']:+.4f}%</span>"
            for x in items
        ])

    def _badge_near(items, is_high=True):
        if not items: return "<span style='color:#999;font-size:13px;'>无</span>"
        color = "#8e44ad" if is_high else "#2980b9"
        bg = "#f4ecf7" if is_high else "#eaf2f8"
        prefix = "距前高" if is_high else "距前低"
        return "".join([
            f"<span style='display:inline-block;background:{bg};color:{color};padding:3px 10px;border-radius:12px;margin:2px 4px 2px 0;font-size:13px;font-weight:bold;'>{x['sym']} {prefix} {x['dist']:.2f}%</span>"
            for x in items
        ])

    # --- 组装 HTML ---
    html = (
        "<div style='background: #ffffff; border-radius: 12px; margin-bottom: 25px; "
        "border: 1px solid #e1e8ed; box-shadow: 0 4px 12px rgba(0,0,0,0.05); overflow: hidden;'>"
        
        # 头部标题区
        "<div style='background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); padding: 15px 20px;'>"
        f"<h3 style='margin: 0; color: #ffc107; font-size: 18px; letter-spacing: 1px;'>🔥 本期市场焦点（{label}）</h3>"
        f"<p style='margin: 5px 0 0 0; color: #bdc3c7; font-size: 12px;'>{vibe}</p>"
        "</div>"
        
        # 数据内容区（表格布局，对齐更工整）
        "<div style='padding: 15px 20px;'>"
        "<table style='width:100%; font-size: 13px; border-collapse: collapse;'>"
        
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; width: 100px; color:#e74c3c; font-weight:bold;'>📈 领涨</td><td style='padding: 10px 0;'>{_badge_gainer(hotspot.get('top_gainers', []))}</td></tr>"
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; color:#27ae60; font-weight:bold;'>📉 领跌</td><td style='padding: 10px 0;'>{_badge_loser(hotspot.get('top_losers', []))}</td></tr>"
        
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; color:#e67e22; font-weight:bold;'>🌡️ 贪婪</td><td style='padding: 10px 0;'>{_badge_rsi(hotspot.get('extreme_greed', []), '#c0392b', '#fdeaea')}</td></tr>"
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; color:#2980b9; font-weight:bold;'>🧊 恐慌</td><td style='padding: 10px 0;'>{_badge_rsi(hotspot.get('extreme_fear', []), '#2471a3', '#eaf2f8')}</td></tr>"
        
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; color:#8e44ad; font-weight:bold;'>💥 异动</td><td style='padding: 10px 0;'>{_badge_vol(hotspot.get('volume_spikes', []))}</td></tr>"
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; color:#d35400; font-weight:bold;'>💰 费率</td><td style='padding: 10px 0;'>{_badge_fr(hotspot.get('funding_extreme', []))}</td></tr>"
        
        f"<tr style='border-bottom: 1px dashed #eee;'><td style='padding: 10px 0; color:#8e44ad; font-weight:bold;'>⚡ 逼近前高</td><td style='padding: 10px 0;'>{_badge_near(hotspot.get('near_highs', []), True)}</td></tr>"
        f"<tr><td style='padding: 10px 0; color:#2980b9; font-weight:bold;'>🛡️ 逼近前低</td><td style='padding: 10px 0;'>{_badge_near(hotspot.get('near_lows', []), False)}</td></tr>"
        
        "</table></div></div>"
    )
    return html


# ============================================================
# 十一、标的独立卡片
# ============================================================
def build_symbol_block(r):
    md = r.get("market_data", {})
    cp = r.get("current_price")
    cp_str = f"${cp:.4f}" if isinstance(cp, (int, float)) else "N/A"
    is_spot_mode = md.get("data_mode") == "spot"

    for sname, sr_obj in r.get("strategy_results", {}).items():
        if sr_obj and sr_obj.get("error"):
            return build_error_card(r, sr_obj["error"])

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

    # 传入 regime 让面板提示与参谋长解读一致
    regime_for_panel = None
    for _sname, _sr in r.get("strategy_results", {}).items():
        if _sr and _sr.get("regime"):
            regime_for_panel = _sr.get("regime")
            break
    html += f"<div style='padding: 0 20px;'>{render_multi_tf_panel(md, regime=regime_for_panel)}</div>"

    # 市场状态面板
    regime = active_sr.get("regime") if active_sr else None
    regime_desc = active_sr.get("regime_desc") if active_sr else None
    momentum_4h = active_sr.get("momentum_4h") if active_sr else None
    allowed = active_sr.get("allowed_tracks", []) if active_sr else []
    forbidden = active_sr.get("forbidden_tracks", []) if active_sr else []

    if regime:
        regime_label = REGIME_MAP.get(regime, regime)
        momentum_cn = MOMENTUM_4H_MAP.get(momentum_4h, "未知")

        html += "<div style='margin: 15px; padding: 12px; background:#f0f4f8; border-left:5px solid #2c3e50; border-radius:0 8px 8px 0;'>"
        html += f"<p style='margin:0; font-size:15px; font-weight:bold; color:#2c3e50;'>🎛️ 市场状态：{regime_label}（4H动能：{momentum_cn}）</p>"
        html += f"<p style='margin:5px 0 0 0; font-size:13px; color:#555;'>{regime_desc}</p>"
        if allowed:
            allowed_str = "、".join([TRACK_NAME_MAP.get(t, t) for t in allowed])
            html += f"<p style='margin:5px 0 0 0; font-size:13px; color:#27ae60;'>✅ 允许轨道：{allowed_str}</p>"
        if forbidden:
            forbidden_str = "、".join([TRACK_NAME_MAP.get(t, t) for t in forbidden])
            html += f"<p style='margin:5px 0 0 0; font-size:13px; color:#e74c3c;'>❌ 禁止轨道：{forbidden_str}</p>"
        if active_sr.get("conflict_note"):
            # 翻译 track_id，避免英文暴露
            _cn_note = translate_track_ids(active_sr["conflict_note"])
            html += f"<p style='margin:5px 0 0 0; font-size:13px; color:#e67e22; font-weight:bold;'>{_cn_note}</p>"
        html += "</div>"

    if is_triggered:
        ta = active_sr.get(track_key, {})
        max_score = ta.get("max", 6)

        html += f"<div style='padding: 20px;'>"
        html += f"<h3 style='color: {border_color}; margin-top:0; font-size: 20px;'>✅ {track_name}（得分 {ta.get('score',0)}/{max_score}）</h3>"
        html += f"<p style='background:#fff0f0; padding:10px; border-radius:5px;'><b>参谋长理由：</b>{active_sr.get('reason','')}</p>"
        html += "<p style='font-weight:bold; border-bottom: 2px dashed #eee; padding-bottom:5px;'>🔍 狙击细节：</p><ul style='font-size: 14px; color: #444;'>"
        html += render_track_details(ta.get("details", {}), max_score)
        html += "</ul>"

        entry_plan = active_sr.get("entry_plan")
        if triggered_direction == "spot_warning":
            html += "<div style='background:#fff3cd; padding:15px; border-left:5px solid #e67e22; border-radius:0 8px 8px 0; margin-top:15px;'>"
            html += "<p style='margin:0; color:#e67e22; font-weight:bold;'>⚠️ 现货模式无法做空。这是逃顶预警信号，建议现有多单分批止盈减仓，不要再追多。</p>"
            html += "</div>"
        elif entry_plan:
            html += render_entry_plan(entry_plan, triggered_direction, cp, cp_str, md)

        html += generate_commander_comment(r, is_triggered=True)
        html += "</div>"
    else:
        html += f"<div style='padding: 20px;'>"
        html += f"<p style='color:#666; font-size:15px;'>当前标的尚未触发开枪信号，以下是各轨道的推演情况：</p>"
        for sname, sr_obj in r.get("strategy_results", {}).items():
            if not sr_obj:
                html += (
                    "<div style='margin-top:15px; padding:10px; "
                    "border-left:4px solid #d32f2f; background:#ffebee; border-radius:0 5px 5px 0;'>"
                    f"<p style='margin:0; color:#c62828; font-weight:bold;'>"
                    f"❌ 策略 {sname} 未返回结果</p></div>"
                )
                continue

            html += f"<div style='margin-top:15px; padding-left:10px; border-left:4px solid #8e44ad; background:#fafafa; padding:10px; border-radius:0 5px 5px 0;'>"
            if sr_obj.get("error"):
                html += (
                    "<div style='background:#ffebee; padding:10px; border-radius:5px; margin-bottom:8px;'>"
                    f"<p style='margin:0; color:#c62828; font-weight:bold;'>"
                    f"❌ 策略 {sname} 执行异常：{sr_obj['error']}</p></div>"
                )

            html += f"<p style='margin-top:0; font-weight:bold; color:#8e44ad;'>策略推演：{sname}</p>"
            track2_name = "🟢 现货逃顶预警" if is_spot_mode else "🔪 见顶做空 + 📉 顺势做空"
            for tk, tn in [("track_1", "🚀 底部突破做多"),
                           ("track_2", track2_name),
                           ("track_3", "🩸 暴跌反弹做多"),
                           ("track_4", "🎯 趋势回踩做多")]:
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


# ============================================================
# 十二、总览面板
# ============================================================
def build_dashboard(results, triggered_list, untriggered_list):
    triggered_syms = [r['symbol'].replace('_USDT','') for r in triggered_list]
    untriggered_syms = [r['symbol'].replace('_USDT','') for r in untriggered_list if r.get("status") == "ok"]
    error_syms = [
        r['symbol'].replace('_USDT','') for r in results
        if any(sr and sr.get("error") for sr in r.get("strategy_results", {}).values())
        or r.get("status") == "error"
    ]
    html = "<div style='background:#fff; border:2px solid #34495e; border-radius: 10px; padding: 15px; margin-bottom: 25px;'>"
    html += "<h3 style='margin-top:0; color:#2c3e50; border-bottom:2px dashed #eee; padding-bottom:10px;'>📋 本期监控全景图</h3>"
    if triggered_syms:
        badges = "".join([f"<span style='background:#d32f2f;color:white;padding:3px 8px;border-radius:5px;font-weight:bold;margin:2px;'>{s}</span>" for s in triggered_syms])
        html += f"<p style='font-size: 16px; color: #e74c3c;'><b>🚨 触发信号：</b> {badges}</p>"
    else:
        html += f"<p style='font-size: 16px; color: #888;'><b>🚨 触发信号：</b> 无。</p>"
    if error_syms:
        badges = "".join([f"<span style='background:#ffebee;color:#c62828;padding:3px 8px;border-radius:5px;margin:2px;border:1px solid #d32f2f;'>{s}</span>" for s in error_syms])
        html += f"<p style='font-size: 14px; color: #c62828;'><b>❌ 策略异常：</b> {badges}</p>"
    if untriggered_syms:
        badges = "".join([f"<span style='background:#ecf0f1;color:#2c3e50;padding:3px 8px;border-radius:5px;margin:2px;'>{s}</span>" for s in untriggered_syms])
        html += f"<p style='font-size: 14px; color: #555;'><b>🔮 盘面推演：</b> {badges}</p>"
    html += "</div>"
    return html


# ============================================================
# 十三、动态标题
# ============================================================
def _fmt_pct(v):
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "?"


def generate_dynamic_subject(triggered_list, untriggered_list, results, hotspot=None):
    now = datetime.now(BJT)
    hour = now.hour
    from report_lexicon import judge_session, SESSION_MAP
    session_key = judge_session(hour)
    session_label = SESSION_MAP.get(session_key, {}).get("label", "")

    tr_syms = [r['symbol'].replace('_USDT', '') for r in triggered_list]

    all_directions, all_sub = [], []
    long_syms, short_syms = [], []
    max_rsi, min_rsi = 0, 100
    greedy_syms, fear_syms = [], []

    for r in results:
        if r.get("status") != "ok":
            continue
        md = r.get("market_data", {})
        sym = r["symbol"].replace("_USDT", "")
        rsi = md.get("rsi")
        if rsi is not None:
            if rsi > max_rsi: max_rsi = rsi
            if rsi < min_rsi: min_rsi = rsi
            if rsi >= 70: greedy_syms.append(sym)
            if rsi <= 30: fear_syms.append(sym)
        for sr in r.get("strategy_results", {}).values():
            if sr and sr.get("triggered"):
                d = sr.get("direction")
                all_directions.append(d)
                if d == "short":
                    all_sub.append(sr.get("track_2", {}).get("sub_type"))
                    short_syms.append(sym)
                elif d and d.startswith("long"):
                    long_syms.append(sym)

    pool = None
    kwargs = {}

    if triggered_list:
        if len(triggered_list) >= 2 and long_syms and short_syms:
            pool = SUBJECT_POOL["multi_trigger_both"]
            kwargs = {
                "session": session_label, "n": len(triggered_list),
                "long_syms": "、".join(long_syms[:2]),
                "short_syms": "、".join(short_syms[:2]),
            }
        elif len(triggered_list) >= 2:
            pool = SUBJECT_POOL["multi_trigger_same"]
            kwargs = {
                "session": session_label, "n": len(triggered_list),
                "syms": "、".join(tr_syms[:3]),
            }
        else:
            sym = tr_syms[0]
            rsi_v = fr_v = fp_v = adx_v = kdj_v = rh_v = "?"
            for r in triggered_list:
                md = r.get("market_data", {})
                rsi_v = _fmt_pct(md.get("rsi"))
                fr_v = _fmt_pct(md.get("funding_rate"))
                fp_v = f"{md.get('funding_percentile'):.1%}" if md.get("funding_percentile") is not None else "?"
                adx_v = _fmt_pct(md.get("adx"))
                kdj_v = _fmt_pct((md.get("kdj_1h") or {}).get("j"))
                rh_v = _fmt_pct(md.get("recent_high"))
                break

            is_short = any(d == "short" for d in all_directions)
            if is_short:
                pool = SUBJECT_POOL["single_trigger_short"]
            else:
                pool = SUBJECT_POOL["single_trigger_long"]

            kwargs = {
                "session": session_label, "sym": sym,
                "rsi": rsi_v, "rsi_1h": rsi_v, "fr": fr_v, "fp": fp_v,
                "adx": adx_v, "kdj_j": kdj_v, "rh": rh_v, "pct": "?",
            }
    else:
        if greedy_syms or (max_rsi >= 70):
            pool = SUBJECT_POOL["no_trigger_greedy"]
            kwargs = {
                "sym": greedy_syms[0] if greedy_syms else "市场",
                "syms": "、".join(greedy_syms[:3]) or "全市场",
                "rsi": _fmt_pct(max_rsi),
            }
        elif fear_syms or (min_rsi <= 30):
            pool = SUBJECT_POOL["no_trigger_panic"]
            kwargs = {
                "sym": fear_syms[0] if fear_syms else "市场",
                "syms": "、".join(fear_syms[:3]) or "全市场",
                "rsi": _fmt_pct(min_rsi),
            }
        else:
            pool = SUBJECT_POOL["no_trigger_quiet"]
            kwargs = {"session": session_label}

    try:
        subject = _pick_unique_subject(pool) if pool else ""
        subject = fill(subject, **kwargs)
    except Exception:
        subject = f"【参谋长推演】{session_label}盘报告"

    return subject or f"【参谋长推演】{session_label}盘报告"


# ============================================================
# 十四、报告主入口
# ============================================================
def build_report(results, active_strategies, watchlist, hotspot=None):
    now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M")
    triggered_list, untriggered_list = [], []
    for r in results:
        has = any(sr and sr.get("triggered") for sr in r.get("strategy_results", {}).values())
        (triggered_list if has else untriggered_list).append(r)

    subject = generate_dynamic_subject(triggered_list, untriggered_list, results, hotspot=hotspot)

    html = "<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#333;padding:10px; background-color:#f4f6f8;'>"
    html += AD_BANNER
    html += "<div style='background:white; padding:20px; border-radius:10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);'>"
    html += "<h2 style='border-bottom: 3px solid #e74c3c; padding-bottom: 10px; color:#2c3e50;'>📊 参谋长多周期共振报告</h2>"
    html += f"<p style='color:#666;'><b>时间：</b>{now} | <b>策略：</b>{', '.join(active_strategies)} | <b>周期：</b>1D · 4H · 1H · 30m</p>"

    try:
        html += build_hotspot_section(hotspot)
    except Exception:
        pass

    html += build_dashboard(results, triggered_list, untriggered_list)

    if triggered_list:
        html += "<h3 style='color:#e74c3c; border-left:5px solid #e74c3c; padding-left:10px; font-size:22px; margin-top:30px;'>🚨 参谋长开枪警告</h3>"
        for r in triggered_list:
            try:
                html += build_symbol_block(r)
            except Exception as e:
                html += build_error_card(r, f"报告渲染失败：{type(e).__name__}: {e}")

    if untriggered_list:
        html += "<hr><h3 style='color:#27ae60; border-left:5px solid #27ae60; padding-left:10px; font-size:20px; margin-top:30px;'>🔮 盘面推演（未触发）</h3>"
        for r in untriggered_list:
            try:
                html += build_symbol_block(r)
            except Exception as e:
                html += build_error_card(r, f"报告渲染失败：{type(e).__name__}: {e}")

    html += build_unsupported_section(results)
    html += build_glossary_section()
    html += build_risk_warning()
    html += "<p style='text-align:center; color:#e67e22; font-weight:bold; font-size:15px; margin-top:20px;'>👉 点赞、转发、关注「牛来参谋长」！</p>"
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
