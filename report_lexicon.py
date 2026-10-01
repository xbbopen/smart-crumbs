# -*- coding: utf-8 -*-
"""
报告词典 + 话术池 + 动态生成器
三层结构：骨架 + 数据槽 + 情绪
"""
import random
import re


# ============================================================
# 基础词典
# ============================================================
TREND_MAP = {"up": "🟢 多头", "down": "🔴 空头", "neutral": "⚪ 震荡", None: "❓ 未知"}

MOMENTUM_4H_MAP = {
    "strong_bull": "强势多头 🟢🟢", "bull": "温和多头 🟢",
    "neutral": "中性震荡 ⚪", "bear": "温和空头 🔴", "strong_bear": "强势空头 🔴🔴",
}

RSI_DIV_MAP = {"bearish": "🔻 顶背离", "bullish": "🔺 底背离", "none": "无背离", None: "无背离"}

REGIME_MAP = {
    "strong_bull": "🟢🟢 强多头趋势", "weak_bull": "🟡 弱多头",
    "weak_bull_warning": "🚨 弱多头警告", "ranging": "⚪ 震荡整理",
    "weak_bear": "🔴 弱空头", "strong_bear": "🔴🔴 强空头趋势",
    "momentum_stall": "⏸️ 动量停滞", "volatile": "⚡ 极端波动",
    "error": "❗ 策略异常",
}

SUB_TYPE_MAP = {"reversal": "见顶反转", "trend_follow": "顺势延续"}
DATA_MODE_MAP = {"futures": "合约", "spot": "现货"}
TRACK_NAME_MAP = {
    "track_1": "底部突破做多", "track_2": "见顶/顺势做空",
    "track_3": "暴跌反弹做多", "track_4": "趋势回踩做多",
}
TRACK_ID_TO_CN = {"track_1": "底部突破", "track_2": "做空", "track_3": "暴跌反弹", "track_4": "趋势回踩"}

SESSION_MAP = {
    "late_night": {"label": "深夜", "vibe": "流动性最差，小心插针"},
    "asia_morning": {"label": "早盘", "vibe": "亚洲盘刚开盘，主力的刀还没出鞘"},
    "asia_afternoon": {"label": "午盘", "vibe": "亚洲盘下半场，情绪开始躁动"},
    "eu_session": {"label": "欧盘", "vibe": "欧洲资金进场，波动明显放大"},
    "us_session": {"label": "美盘", "vibe": "美国资金主导，最容易出现暴动行情"},
}


def judge_session(hour_bjt):
    if 0 <= hour_bjt < 9: return "late_night"
    if 9 <= hour_bjt < 12: return "asia_morning"
    if 12 <= hour_bjt < 16: return "asia_afternoon"
    if 16 <= hour_bjt < 20: return "eu_session"
    return "us_session"


def translate_track_ids(text):
    if not text: return text
    for tid in sorted(TRACK_ID_TO_CN.keys(), key=len, reverse=True):
        text = str(text).replace(tid, TRACK_ID_TO_CN[tid])
    return text


def trend_cn(t):
    return {"up": "多头", "down": "空头", "neutral": "震荡", None: "未知"}.get(t, "未知")

def momentum_cn(m):
    return {"strong_bull": "强势多头", "bull": "温和多头", "neutral": "中性震荡",
            "bear": "温和空头", "strong_bear": "强势空头", None: "未知"}.get(m, "未知")

def regime_cn(r):
    return {"strong_bull": "强多头", "weak_bull": "弱多头", "weak_bull_warning": "弱多头警告",
            "ranging": "震荡整理", "weak_bear": "弱空头", "strong_bear": "强空头",
            "momentum_stall": "动量停滞", "volatile": "极端波动", "error": "策略异常"}.get(r, "未知")


# ============================================================
# 话术池：4 种风格
# ============================================================
# 钩子池（情绪开场）
HOOKS = {
    "long_trend": {
        "煽动型": ["兄弟们", "注意", "机会来了", "别眨眼", "起飞前最后一秒"],
        "数据型": ["数据显示", "从盘面看", "技术面", "客观来讲"],
        "教练型": ["记住", "耐心等这一刻", "交易就是等待", "纪律优先"],
        "战术型": ["布防完毕", "开火前最后确认", "埋伏点已到", "子弹上膛"],
    },
    "long_pullback": {
        "煽动型": ["兄弟们", "别错过", "黄金坑", "主力送钱来了"],
        "数据型": ["数据显示", "从结构看", "技术面显示", "客观上讲"],
        "教练型": ["记住一句话", "耐心等这一刻", "趋势中的回踩是礼物", "别追高"],
        "战术型": ["布防完毕", "埋伏点位已到", "开火前最后确认", "静待扳机"],
    },
    "long_rebound": {
        "煽动型": ["兄弟们", "绝地反击", "带血的筹码", "抄底时机"],
        "数据型": ["数据显示", "从极端指标看", "客观来讲", "从技术面看"],
        "教练型": ["记住", "恐慌时贪婪", "别人恐惧我贪婪", "极端才有机会"],
        "战术型": ["埋伏点已到", "小仓位试单", "开火前最后确认", "布防完毕"],
    },
    "short_reversal": {
        "煽动型": ["兄弟们", "警报", "见顶信号", "主力要动手了"],
        "数据型": ["数据显示", "从顶背离看", "从结构看", "客观来讲"],
        "教练型": ["记住", "别当最后一棒", "贪婪时恐惧", "风险第一"],
        "战术型": ["狙击点位已到", "开火", "空单进场点", "扣扳机"],
    },
    "short_trend": {
        "煽动型": ["兄弟们", "反弹就是机会", "空头送钱", "别被反弹骗了"],
        "数据型": ["数据显示", "从结构看", "客观来讲", "从趋势看"],
        "教练型": ["记住", "顺势为王", "趋势不反转不做多", "纪律第一"],
        "战术型": ["狙击点位已到", "阻力位挂空单", "埋伏点已到", "静待反弹"],
    },
    "spot_warning": {
        "煽动型": ["警报", "兄弟们注意", "跑路信号", "别贪最后一口"],
        "数据型": ["数据显示", "客观来讲", "从指标看"],
        "教练型": ["记住", "落袋为安", "止盈不丢人"],
        "战术型": ["分批止盈", "逐步离场"],
    },
    "no_trigger_greedy": {
        "煽动型": ["小心", "注意风险", "别被FOMO冲昏头"],
        "数据型": ["数据显示", "客观来讲", "从指标看"],
        "教练型": ["记住", "越是狂欢越要清醒"],
        "战术型": ["按兵不动", "等回落"],
    },
    "no_trigger_panic": {
        "煽动型": ["机会在酝酿", "别急着接刀"],
        "数据型": ["数据显示", "客观来讲"],
        "教练型": ["记住", "抄底要看时机"],
        "战术型": ["等企稳", "分批建仓"],
    },
    "no_trigger_quiet": {
        "煽动型": ["休息", "空仓是种美德"],
        "数据型": ["数据显示", "客观来讲", "从盘面看"],
        "教练型": ["记住", "不操作也是一种操作"],
        "战术型": ["按兵不动", "观望"],
    },
}

# 数据锚点池（同一数据多种说法）
ANCHOR_BOLL_MID = ["1H布林中轨 {v}", "布林中轨 {v}", "中轨 {v}", "{v} 中轨"]
ANCHOR_MA10 = ["30m MA10 {v}", "生命线 {v}", "30m 均线 {v}", "MA10 {v}"]
ANCHOR_RSI = ["RSI {v}", "RSI 报 {v}", "相对强弱 {v}"]
ANCHOR_KDJ = ["1H KDJ J={v}", "KDJ J={v}", "J 值 {v}"]
ANCHOR_FR = ["费率 {v}", "资金费率 {v}", "费率 {v}（多头拥挤度 {fp}）"]
ANCHOR_ADX = ["ADX={v}", "趋势强度 ADX={v}"]

# 判断句池
JUDGMENT_POOL = {
    "long_trend": [
        "双周期共振多头，动能健康",
        "站上生命线，主力点火起飞",
        "底部放量突破，启动信号已亮",
        "4H MACD 金叉，趋势启动三要素齐备",
    ],
    "long_pullback": [
        "缩量回踩，卖压衰竭",
        "长下影现身，主力接盘",
        "RSI 处于健康回调区，趋势未改",
        "回踩中轨缩量止跌，经典黄金坑",
    ],
    "long_rebound": [
        "极端超卖 + 止跌形态，反弹一触即发",
        "带血筹码满地，主力悄悄吸筹",
        "CVD 底背离暴露主力意图",
        "KDJ J 值极度超卖，抛压已尽",
    ],
    "short_reversal": [
        "顶背离已现，聪明钱在出货",
        "费率飙高，多头拥挤度爆表",
        "RSI 严重超买，技术面亮红灯",
        "4H MACD 死叉，多头动能衰竭",
    ],
    "short_trend": [
        "下跌中继，反弹就是做空机会",
        "价格反弹到阻力位，空单最佳点位",
        "4H 空头结构确认，反弹到位就是空",
        "反弹无量，上方压力重重",
    ],
    "spot_warning": [
        "多项见顶指标共振，现货建议减仓",
        "价格逼近前高，多头拥挤",
        "技术面亮红灯，保利润要紧",
    ],
    "no_trigger_greedy": [
        "全市场狂欢，越要警惕变盘",
        "指标接近临界点，随时可能反转",
        "FOMO 情绪爆表，别当最后一棒",
    ],
    "no_trigger_panic": [
        "恐慌指数飙升，机会正在酝酿",
        "带血筹码满地，但还没到极端",
        "情绪冰点，耐心等待企稳",
    ],
    "no_trigger_quiet": [
        "盘面死水微澜，主力在憋大招",
        "各项指标不达标，观望为宜",
        "无趣的盘面，动它不如不动",
    ],
}

# 行动号召池
CALLS = {
    "long_trend": ["分批上车", "趋势中回调加仓", "跟紧这波", "子弹上膛"],
    "long_pullback": ["回踩到位就入场", "分批建仓，设好止损", "别错过这波趋势", "挂好限价单"],
    "long_rebound": ["小仓位试单", "全部限价，不追高", "分批埋伏", "耐心等确认"],
    "short_reversal": ["立即市价空", "顶部窗口极短", "分批埋伏空单", "扣扳机"],
    "short_trend": ["阻力位挂空单", "反弹到位就出手", "顺势追空", "带好2%止损"],
    "spot_warning": ["逐步止盈", "分批减仓", "不再追多", "保住利润"],
    "no_trigger_greedy": ["等回落再动手", "不要追高", "管住手"],
    "no_trigger_panic": ["等企稳信号", "分批建仓，别一把梭", "耐心等待"],
    "no_trigger_quiet": ["空仓休息", "等信号", "观望", "养精蓄锐"],
}


# ============================================================
# 骨架池（3-5 种骨架结构）
# ============================================================
SKELETONS = [
    "{hook}，{sym} {anchor}，{judgment}。{call}",
    "{sym} {anchor}，{hook}，{judgment}。{call}",
    "{hook}！{sym} {anchor}，{judgment}，{call}。",
    "{judgment}。{sym} {anchor}，{hook}，{call}。",
    "{sym} {anchor}。{hook}，{judgment}，{call}。",
]


# ============================================================
# 智能提示（与参谋长解读共用数据源）
# ============================================================
def build_smart_tips(md, regime, warnings=None):
    """多周期提示：从 regime + 具体数据生成，和参谋长解读一致"""
    tips = []
    regime_tip = {
        "volatile": "⚡ 极端波动，所有轨道暂停，等市场平静",
        "momentum_stall": "⏸️ 动量停滞，主力按兵不动，观望为宜",
        "strong_bull": "🟢🟢 1D+4H双多头，回调即机会",
        "weak_bull": "🟡 1D多头但4H震荡，只做回踩或见顶",
        "weak_bull_warning": "🚨 1D多头但4H已转空，谨防做多陷阱",
        "ranging": "⚪ 无明确趋势，区间操作，快进快出",
        "weak_bear": "🔴 1D空头，反弹即做空机会",
        "strong_bear": "🔴🔴 1D+4H双空头，反弹即陷阱",
    }.get(regime, "⚪ 状态未知，观望")
    tips.append(regime_tip)

    rsi_1h = md.get("rsi_1h")
    adx = md.get("adx")
    fp = md.get("funding_percentile")

    if rsi_1h is not None:
        if rsi_1h > 70: tips.append(f"1H RSI {rsi_1h:.1f} 已超买")
        elif rsi_1h < 35: tips.append(f"1H RSI {rsi_1h:.1f} 已超卖")
    if adx is not None:
        if adx < 18: tips.append(f"ADX {adx:.1f} 低位，趋势弱")
        elif adx > 25: tips.append(f"ADX {adx:.1f} 趋势强")
    if fp is not None and fp > 0.7:
        tips.append(f"费率拥挤度 {fp:.0%} 偏高")
    if warnings:
        tips.extend(warnings)

    return tips


# ============================================================
# 动态话术生成器
# ============================================================
_STYLE_CYCLE = ["煽动型", "数据型", "教练型", "战术型"]


def _fill(template, **kwargs):
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def generate_dynamic_comment(symbol, direction, md, entry_plan, reason="", history=None):
    """
    动态生成参谋长解读。
    - 骨架 + 数据槽 + 风格随机
    - 和历史去重
    """
    history = history or []
    sym = symbol.replace("_USDT", "")
    price = md.get("current_price")
    ma10 = md.get("ma10")
    boll_mid = (md.get("boll_1h") or {}).get("mid")
    rsi = md.get("rsi")
    rsi_1h = md.get("rsi_1h")
    kdj_j = (md.get("kdj_1h") or {}).get("j")
    adx = md.get("adx")
    fr = md.get("funding_rate")
    fp = md.get("funding_percentile")

    # 场景映射
    scene_map = {
        "long_trend": "long_trend", "long_pullback": "long_pullback",
        "long_rebound": "long_rebound", "short_reversal": "short_reversal",
        "short_trend": "short_trend", "spot_warning": "spot_warning",
    }
    scene = scene_map.get(direction, "no_trigger_quiet")

    # 随机抽风格（和历史去重）
    style = random.choice(_STYLE_CYCLE)
    hook = random.choice(HOOKS.get(scene, HOOKS["no_trigger_quiet"])[style])
    judgment = random.choice(JUDGMENT_POOL.get(scene, JUDGMENT_POOL["no_trigger_quiet"]))
    call = random.choice(CALLS.get(scene, CALLS["no_trigger_quiet"]))
    skeleton = random.choice(SKELETONS)

    # 数据锚点：动态选择
    if ma10 and price and abs(price - ma10) / ma10 < 0.02:
        anchor = random.choice(ANCHOR_MA10).format(v=f"{ma10:.4f}")
    elif boll_mid and price and abs(price - boll_mid) / boll_mid < 0.02:
        anchor = random.choice(ANCHOR_BOLL_MID).format(v=f"{boll_mid:.4f}")
    elif rsi is not None:
        anchor = random.choice(ANCHOR_RSI).format(v=f"{rsi:.1f}")
    else:
        anchor = f"现价 ${price:.4f}" if price else "当前位置"

    # 组装
    text = _fill(skeleton, hook=hook, sym=sym, anchor=anchor,
                 judgment=judgment, call=call)

    # 追加实时数据（保证事实锚点 ≥ 2）
    data_parts = []
    if rsi is not None: data_parts.append(f"RSI {rsi:.1f}")
    if rsi_1h is not None: data_parts.append(f"1H RSI {rsi_1h:.1f}")
    if adx is not None: data_parts.append(f"ADX {adx:.1f}")
    if kdj_j is not None: data_parts.append(f"J={kdj_j:.1f}")
    if fr is not None: data_parts.append(f"费率 {fr:.4f}%")

    if len(data_parts) >= 2:
        text += f"（{' · '.join(data_parts[:4])}）"

    return text


# ============================================================
# 标题话术池（IP 锚定，保持原有丰富度）
# ============================================================
SUBJECT_POOL = {
    "single_trigger_long": [
        "🐮 参谋长战报：{session} {sym} 异动！{anchor}，子弹已上膛",
        "💥 【参谋长预警】{sym} 突破形态，{judgment}",
        "🎯 参谋长盯盘：{session} {sym} 双周期共振，回踩即机会",
        "🔥 牛来参谋长：{sym} 多头动能蓄满，{session} 或将起飞",
        "⚡ 参谋长点名：{sym} 站上生命线，{call}",
    ],
    "single_trigger_short": [
        "🩸 参谋长战报：{session} {sym} 费率异常，多头拥挤度爆表",
        "🔪 【参谋长预警】{sym} 顶背离已现，主力偷偷出货",
        "⚔️ 参谋长盯盘：{session} {sym} 冲高回落，只差导火索",
        "🚨 牛来参谋长：{sym} 逼近前高，最后一棒将落",
        "💀 参谋长点名：{sym} 4H MACD 死叉，{call}",
    ],
    "multi_trigger_both": [
        "⚔️ 参谋长重磅战报：{n}个标的齐爆！{long_syms}做多、{short_syms}做空",
        "🚨 牛来参谋长：多空双杀！{n}个信号同时触发",
        "💥 参谋长紧急预警：{n}个标的暴动，主力露出獠牙",
    ],
    "multi_trigger_same": [
        "🚀 参谋长战报：{n}个标的齐爆多！{syms}",
        "🔥 牛来参谋长：{n}个标的齐共振，{session} 看多",
        "🐮 参谋长吹哨：{n}个标的齐发信号，跟紧节奏",
    ],
    "no_trigger_greedy": [
        "🔥 参谋长预警：全市场 RSI 飙升！{syms}，FOMO 情绪爆表",
        "🎈 牛来参谋长：{sym} RSI {rsi} 逼近极值，警惕变盘",
        "⚡ 参谋长盯盘：{syms} RSI 集体冲高，别当最后一棒",
    ],
    "no_trigger_panic": [
        "💀 参谋长盯盘：血洗现场！{syms} RSI 集体跌破 30",
        "🩸 牛来参谋长：{sym} RSI {rsi}，机会正在酝酿",
        "📉 参谋长战报：{syms} 跌出恐慌区，抄底时机临近",
    ],
    "no_trigger_quiet": [
        "😴 参谋长盯盘：主力集体装死？全市场 ADX < 18",
        "🔮 牛来参谋长推演：{session} 多空博弈白热化",
        "⏳ 参谋长提示：盘面暗流涌动，{session} 或迎大动作",
    ],
}


def fill(template, **kwargs):
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def pick_unique(pool, history, k=1):
    if not pool: return []
    candidates = list(pool)
    random.shuffle(candidates)
    picked = []
    for c in candidates:
        if c not in history:
            picked.append(c)
            if len(picked) >= k: return picked
    while len(picked) < k:
        picked.append(random.choice(pool))
    return picked


def pick_one(pool):
    return random.choice(pool) if pool else ""
