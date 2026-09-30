# -*- coding: utf-8 -*-
"""
报告词典 + 话术池。
本模块只提供纯数据与纯函数，不引入任何副作用，方便单测和复用。
"""
import random
import re

# ============================================================
# 一、词典（英文 → 中文）
# ============================================================
TREND_MAP = {
    "up": "🟢 多头", "down": "🔴 空头",
    "neutral": "⚪ 震荡", None: "❓ 未知",
}

MOMENTUM_4H_MAP = {
    "strong_bull": "强势多头 🟢🟢",
    "bull": "温和多头 🟢",
    "neutral": "中性震荡 ⚪",
    "bear": "温和空头 🔴",
    "strong_bear": "强势空头 🔴🔴",
}

RSI_DIV_MAP = {
    "bearish": "🔻 顶背离",
    "bullish": "🔺 底背离",
    "none": "无背离",
    None: "无背离",
}

REGIME_MAP = {
    "strong_bull": "🟢🟢 强多头趋势",
    "weak_bull": "🟢 弱多头",
    "weak_bull_warning": "🚨 弱多头（警告）",
    "ranging": "⚪ 震荡整理",
    "weak_bear": "🔴 弱空头",
    "strong_bear": "🔴🔴 强空头趋势",
    "momentum_stall": "⏸️ 动量停滞",
    "error": "❗ 策略异常",
}

SUB_TYPE_MAP = {
    "reversal": "见顶反转",
    "trend_follow": "顺势延续",
}

DATA_MODE_MAP = {
    "futures": "合约",
    "spot": "现货",
}

TRACK_NAME_MAP = {
    "track_1": "底部突破做多",
    "track_2": "见顶/顺势做空",
    "track_3": "暴跌反弹做多",
    "track_4": "趋势回踩做多",
}

# 时段（北京时间）
SESSION_MAP = {
    "late_night":     {"label": "深夜", "vibe": "流动性最差，小心插针"},
    "asia_morning":   {"label": "早盘", "vibe": "亚洲盘刚开盘，主力的刀还没出鞘"},
    "asia_afternoon": {"label": "午盘", "vibe": "亚洲盘下半场，情绪开始躁动"},
    "eu_session":     {"label": "欧盘", "vibe": "欧洲资金进场，波动明显放大"},
    "us_session":     {"label": "美盘", "vibe": "美国资金主导，最容易出现暴动行情"},
}


def judge_session(hour_bjt: int) -> str:
    if 0 <= hour_bjt < 9:
        return "late_night"
    if 9 <= hour_bjt < 12:
        return "asia_morning"
    if 12 <= hour_bjt < 16:
        return "asia_afternoon"
    if 16 <= hour_bjt < 20:
        return "eu_session"
    return "us_session"


# ============================================================
# 二、值翻译函数（供 v1_default.py 使用）
# ============================================================
def trend_cn(t):
    """up / down / neutral → 中文。"""
    return {
        "up": "多头",
        "down": "空头",
        "neutral": "震荡",
        None: "未知",
    }.get(t, str(t) if t else "未知")


def momentum_cn(m):
    """4H 动能值 → 中文。"""
    return {
        "strong_bull": "强势多头",
        "bull": "温和多头",
        "neutral": "中性震荡",
        "bear": "温和空头",
        "strong_bear": "强势空头",
        None: "未知",
    }.get(m, str(m) if m else "未知")


def regime_cn(r):
    """市场状态 → 中文。"""
    return {
        "strong_bull": "强多头",
        "weak_bull": "弱多头",
        "weak_bull_warning": "弱多头警告",
        "ranging": "震荡整理",
        "weak_bear": "弱空头",
        "strong_bear": "强空头",
        "momentum_stall": "动量停滞",
        "error": "策略异常",
        None: "未知",
    }.get(r, str(r) if r else "未知")


def sub_type_cn(s):
    """track_2 子类型 → 中文。"""
    return {
        "reversal": "见顶反转",
        "trend_follow": "顺势延续",
        None: "未知",
    }.get(s, str(s) if s else "未知")


# ============================================================
# 三、通用兜底 sanitize（对任意文本做英文→中文替换）
# ============================================================
_SANITIZE_PAIRS = [
    # 按长度降序，长词先替换，避免子串误伤
    ("strong_bull", "强势多头"),
    ("strong_bear", "强势空头"),
    ("weak_bull_warning", "弱多头警告"),
    ("weak_bull", "弱多头"),
    ("weak_bear", "弱空头"),
    ("momentum_stall", "动量停滞"),
    ("trend_follow", "顺势"),
    ("reversal", "反转"),
    ("spot_warning", "现货逃顶"),
    ("long_pullback", "趋势回踩"),
    ("long_rebound", "暴跌反弹"),
    ("long_trend", "底部突破"),
    ("binance_spot", "币安现货"),
    ("gate_spot", "Gate现货"),
    ("bearish", "顶背离"),
    ("bullish", "底背离"),
    ("neutral", "震荡"),
    ("bull", "多头"),
    ("bear", "空头"),
    ("ranging", "震荡"),
    ("error", "异常"),
    ("track_4", "趋势回踩"),
    ("track_3", "暴跌反弹"),
    ("track_2", "做空"),
    ("track_1", "底部突破"),
    ("spot", "现货"),
    ("futures", "合约"),
    ("long", "做多"),
    ("short", "做空"),
    ("up", "上涨"),
    ("down", "下跌"),
]


def sanitize_text(text):
    """对任意字符串做英文→中文兜底替换，防止英文暴露。"""
    if text is None:
        return text
    result = str(text)
    for en, cn in _SANITIZE_PAIRS:
        pattern = r'\b' + re.escape(en) + r'\b'
        result = re.sub(pattern, cn, result)
    return result


# ============================================================
# 四、标题话术池
# ============================================================
SUBJECT_POOL = {
    "single_trigger_long": [
        "🐮 参谋长战报：{session} {sym} 异动！30m RSI {rsi}，子弹已上膛",
        "💥 【参谋长预警】{sym} 放量突破！RSI {rsi} 温和上行，主力点火",
        "🎯 参谋长盯盘：{session} {sym} 底部长阳 + 4H MACD金叉，双周期共振",
        "🔥 牛来参谋长：{sym} 蓄力完毕！ADX {adx} 趋势明确，这波吃满",
        "⚡ 参谋长点名：{sym} 双周期共振多头，回调就是上车机会",
        "🌙 参谋长伏击：{sym} 回踩布林中轨，主力给的最后一次机会",
        "💎 参谋长看好：{sym} 底部洗盘结束，站上 MA10 启动信号已亮",
        "🐮 牛来参谋长吹哨：{sym} 多头动能蓄满，{session} 或将起飞",
    ],
    "single_trigger_short": [
        "🩸 参谋长战报：{session} {sym} 资金费率 {fr}%，多头拥挤度爆表",
        "🔪 【参谋长预警】{sym} RSI {rsi}，KDJ {kdj_j}，三重超买共振",
        "⚔️ 参谋长盯盘：{session} {sym} CVD 顶背离，主力偷偷出货",
        "🚨 牛来参谋长：{sym} 逼近前高 {rh}，费率 {fp}，最后一棒将落",
        "💀 参谋长点名：{sym} 4H MACD 死叉，反弹到阻力位，空单机会到了",
        "🌪️ 参谋长狙击：{session} {sym} 冲高回落，只差一根导火索",
        "🎯 参谋长预警：{sym} 1H 顶背离 + 费率极端，主力要动手了",
        "❄️ 牛来参谋长吹哨：{sym} 多头狂欢即将结束，{session} 或变盘",
    ],
    "multi_trigger_both": [
        "⚔️ 参谋长重磅战报：{n}个标的齐爆！{long_syms}做多、{short_syms}做空",
        "🚨 牛来参谋长：多空双杀！{long_syms}看多、{short_syms}看空，{session} 炸裂",
        "💥 参谋长紧急预警：{n}个信号同时触发，主力露出獠牙",
        "🔥 参谋长战报：冰火两重天！{n}个标的暴动，你跟不跟？",
        "🐮 牛来参谋长吹哨：{n}个标的齐发信号，难得的大行情",
        "🎯 参谋长发车：{n}发信号齐鸣！{long_syms}、{short_syms}各有戏",
        "⚡ 参谋长盯盘：{session} {n}个标的异动，主力底牌已暴露",
        "🌪️ 牛来参谋长战报：市场暴动！{n}个标的信号触发，跟紧节奏",
    ],
    "multi_trigger_same": [
        "🚀 参谋长战报：{n}个标的齐爆多！{syms}，{session} 或迎大行情",
        "🔥 牛来参谋长：多头信号密集！{syms} 齐共振，{session} 看多",
        "💥 【参谋长预警】{session} {n}个标的做多信号齐发",
        "🎯 参谋长发车：{syms} 多头共振！跟上节奏",
        "⚡ 参谋长盯盘：{n}个标的做多信号，{session} 或暴动",
        "🐮 牛来参谋长吹哨：{n}个标的齐爆，主力做多意图明显",
        "🚨 参谋长战报：{session} 集体异动，{syms} 多头共振",
        "💎 参谋长点名：{syms} 齐发做多信号，{session} 不容错过",
    ],
    "no_trigger_greedy": [
        "🔥 参谋长预警：全市场 RSI 飙升！{syms}，FOMO 情绪已到极限",
        "🎈 牛来参谋长：极度贪婪！{sym} RSI {rsi} 创近期新高",
        "⚡ 参谋长盯盘：市场狂热！{syms} RSI 高位徘徊，小心变盘",
        "🌡️ 参谋长提示：贪婪时刻，{sym} RSI {rsi}，离顶部还有多远？",
        "💥 【参谋长预警】FOMO 情绪爆棚！{syms} 已到警戒线",
        "🎈 牛来参谋长吹哨：全市场发烧，{syms} RSI 集体冲高",
        "🔔 参谋长警报：{sym} RSI {rsi}，主力可能在诱多",
        "⚠️ 参谋长提示：高位风险！{syms} 多项指标进入超买区",
    ],
    "no_trigger_panic": [
        "💀 参谋长盯盘：血洗现场！{syms} RSI 集体跌破 30",
        "🩸 牛来参谋长：极度恐慌！{sym} RSI {rsi}，机会正在酝酿",
        "📉 参谋长战报：市场哀嚎，{syms} 跌出恐慌区，抄底时机临近",
        "🌧️ 参谋长提示：恐慌指数飙升，{sym} RSI {rsi} 是机会的温床",
        "💎 牛来参谋长吹哨：带血的筹码！{syms} RSI 超卖，重点关注",
        "🩸 参谋长预警：全场哀嚎，{syms} 跌破关键支撑，主力在捡便宜",
        "⛈️ 参谋长盯盘：风雨欲来，{sym} RSI {rsi}，越这种时候越要盯紧",
        "💀 牛来参谋长：恐慌盘涌出，{syms} 集体超卖，反弹一触即发",
    ],
    "no_trigger_quiet": [
        "😴 参谋长盯盘：主力集体装死？全市场 ADX < 18，憋大招还是真没戏",
        "🔮 牛来参谋长推演：{session} 多空博弈白热化，主力按兵不动",
        "⏳ 参谋长提示：盘面暗流涌动，{session} 或迎大动作",
        "🎯 参谋长盯盘：{session} 平淡，主力高度控盘，等信号",
        "🐮 牛来参谋长推演：{session} 无明确方向，空仓等机会",
        "😪 参谋长提示：无趣的盘面，{session} 小幅震荡，主力观望",
        "🛡️ 参谋长预警：全市场静默，等一根阳线或阴线定方向",
        "🧘 牛来参谋长：{session} 修整，技术面不达标，动手不如不动",
    ],
}


# ============================================================
# 五、参谋长解读话术池
# ============================================================
COMMENT_POOL = {
    "long_rebound": [
        "带血的筹码满地都是！{sym} RSI 砸到 {rsi}，1H RSI={rsi_1h} 也在超卖区。主力吸筹痕迹藏不住了。",
        "别人恐慌我贪婪。{sym} 从高点回撤 {drawdown}%，CVD 已率先底背离，参谋长子弹已上膛。",
        "{sym} 这波砸盘，砸的不是价格，是散户的心态。RSI {rsi} 是近期最低，抛压已尽。",
        "看这根长下影线——{sym} 在低位有主力接盘，1H KDJ J={kdj_j}。反弹第一枪已响。",
        "极端恐慌时刻！{sym} 30m RSI={rsi}，1H RSI={rsi_1h}。市场在抛售，主力在捡钱。",
        "{sym} 暴跌 {drawdown}%，RSI {rsi} 极度超卖。这种位置不抄底，难道等涨回去再追？",
        "血流成河！{sym} RSI {rsi}，KDJ J 值 {kdj_j}，这是近几个月最狠的一次洗盘。",
        "所有人都在恐慌的时候，{sym} 的 CVD 已悄悄拐头。主力进场的脚印，参谋长看得清清楚楚。",
    ],
    "long_trend": [
        "{sym} 蓄力完毕！站上 MA10，主力点火起飞。这波趋势我们要吃满。",
        "底部信号已现：{sym} 站上 MA10，RSI {rsi} 温和上行，量能配合。",
        "{sym} 双周期共振多头，ADX={adx} 趋势明确，回调就是上车机会。",
        "主力洗盘结束，{sym} 从底部区域反弹，突破在即。",
        "{sym} 放量长阳突破，4H MACD 金叉。这是标准的启动信号。",
        "磨了这么久，{sym} 终于要动了。RSI {rsi} 不高不低，主力控制得刚好。",
        "{sym} 4H EMA20 > EMA50，30m 站上 MA10。趋势启动的三要素齐了。",
        "别人还在犹豫，{sym} 的底部已经悄悄抬升。主力不声不响地建仓。",
    ],
    "long_pullback": [
        "{sym} 双多头共振回踩 1H 布林中轨，这是趋势中难得的黄金坑。",
        "回踩不破 MA10，{sym} 的上升通道依然完好。缩量回踩就是加仓机会。",
        "强势币种不会给你太多上车机会。{sym} 回踩布林中轨，止跌信号已现。",
        "{sym} 1H RSI {rsi_1h} 健康回调，4H 趋势未变。这种坑不跳更待何时。",
        "趋势行情中最贵的就是回踩。{sym} 现在就在送分，接不接？",
        "{sym} 30m 缩量回踩，长下影止跌。多头趋势的回踩就是这么简单。",
        "大方向没变，短期回调是礼物。{sym} 布林中轨支撑有效。",
        "{sym} 1D+4H 双多头，回踩 1H 布林中轨。主力给你最后一次上车机会。",
    ],
    "short_reversal": [
        "{sym} 资金费率飙到 {fr}%，全市场拥挤度 {fp}。主力要割的就是这种 FOMO 多头。",
        "RSI {rsi}，1H RSI={rsi_1h}。技术面亮红灯，{sym} 冲高回落只差导火索。",
        "CVD 顶背离已现——{sym} 价格创新高，主力买盘在偷偷撤。聪明钱在出货。",
        "{sym} 逼近前高 {rh}，1H KDJ J 值 {kdj_j} 严重超买。见顶信号密集出现。",
        "别当最后一棒！{sym} 4H MACD 死叉，多头动能衰竭。",
        "{sym} 放量长上影，1H 顶背离。主力用最后一波诱多，骗接盘侠。",
        "费率 {fp}、RSI {rsi}、KDJ {kdj_j}，三重超买共振。{sym} 的顶可能就在眼前。",
        "多头狂欢即将结束。{sym} 各项指标已拉到极限，接盘的都是韭菜。",
    ],
    "short_trend": [
        "{sym} 4H 空头结构 + 反弹到关键阻力。给空头送钱的机会，顺势追空。",
        "{sym} 反弹到 1H 布林中轨，这是标准的下跌中继做空点。",
        "反弹无量，{sym} 上方压力重重。下跌趋势中，反弹就是做空机会。",
        "{sym} 4H EMA20 < EMA50，中期空头结构确认。反弹到位就是空单进场点。",
        "别被反弹骗了。{sym} 1H RSI={rsi_1h} 中位，跌势未完成。",
        "{sym} 30m 反弹遇阻，长上影线。空头结构下，反抽就是机会。",
        "{sym} 中期下跌趋势明确，反弹到布林中轨附近遇阻。",
        "空头趋势中，反弹不是反转。{sym} 阻力位一到，空单直接挂。",
    ],
    "spot_warning": [
        "警报！{sym} 现货模式出现逃顶信号！RSI={rsi}，价格逼近前高。逐步止盈。",
        "{sym} 各项见顶指标共振，现货无法做空，建议减仓保住利润。",
        "现货玩家注意：{sym} 已到危险区，别追多。手上有的，分批止盈。",
        "{sym} RSI {rsi} 逼近极值，这是顶部特征。现货建议逐步离场。",
        "逃顶信号已现。{sym} 资金费率 {fr}，多头拥挤。现货别贪最后一口。",
        "{sym} 逼近前高 {rh}，1H 超买。现货玩家该考虑跑路了。",
    ],
    "no_trigger_greedy": [
        "全市场都在狂欢，{sym} 也不例外。但参谋长提醒：越是这种时候越要盯紧费率。",
        "{sym} 现在 RSI={rsi}，快到见顶区。别被 FOMO 冲昏头，等它的做空信号。",
        "贪婪时刻！{sym} 指标在临界点徘徊，随时可能变盘。",
        "{sym} RSI {rsi}，离超买就一步。持仓的朋友小心点。",
        "市场情绪高涨，{sym} 各项指标偏热。不追高，等回调。",
        "{sym} 目前处于狂热边缘，RSI={rsi}。管住手，等信号。",
        "所有人都在赚钱的时候，就要警惕了。{sym} RSI {rsi} 偏高。",
        "{sym} 距离顶部还有多少？RSI {rsi} 已在警戒线附近。",
    ],
    "no_trigger_panic": [
        "机会在酝酿！{sym} RSI={rsi}，极度恐慌。子弹已上膛，等企稳信号。",
        "血流成河，{sym} 已跌出恐慌区。这是机会的温床，但别急着接刀。",
        "{sym} RSI {rsi} 近超卖，但还没到极端。再等等。",
        "市场恐慌，{sym} 也不例外。但抄底要看时机，现在为时尚早。",
        "{sym} 跌了不少，RSI {rsi}。信号还不明确，耐心等待。",
        "恐慌指数飙升！{sym} 各项指标进入超卖边缘。机会正在酝酿。",
        "带血的筹码到处都是，{sym} RSI {rsi}。抄底要分批，别一把梭。",
        "{sym} 已跌到情绪冰点，但技术面还没到极端。再观察 1~2 根 K 线。",
    ],
    "no_trigger_quiet": [
        "{sym} 目前垃圾时间，各项指标不达标。空仓休息，等信号。",
        "{sym} 现在 ADX={adx}，主力高度控盘，进去就是送人头。",
        "{sym} 盘面暗流涌动，主力小动作藏不住。耐心等信号。",
        "{sym} 各项指标平淡无奇，不是出手的时候。",
        "别急，{sym} 目前没有明确方向。等突破，等信号。",
        "{sym} 在震荡区间里晃，没有参与价值。观望。",
        "{sym} 目前处于垃圾时间，动它不如不动。",
        "{sym} 各项指标都在中间区域，没有亮点。空仓是种美德。",
    ],
}


# ============================================================
# 六、热点描述话术池
# ============================================================
HOTSPOT_POOL = {
    "top_gainer": [
        "{sym} 一骑绝尘，狂拉 {pct}%",
        "{sym} 强势领涨，涨幅 {pct}%",
        "{sym} 日内飙升 {pct}%，主力发力",
        "{sym} 暴力拉升 {pct}%，全市场瞩目",
    ],
    "top_loser": [
        "{sym} 惨烈杀跌，暴跌 {pct}%",
        "{sym} 领跌全市场，跌幅 {pct}%",
        "{sym} 单日重挫 {pct}%，多头溃败",
        "{sym} 断崖式下跌 {pct}%，血洗多头",
    ],
    "extreme_greed": [
        "{sym} RSI {rsi}（极度贪婪）",
        "{sym} 情绪爆表，RSI 冲上 {rsi}",
        "{sym} RSI 破 {rsi}，超买警示",
    ],
    "extreme_fear": [
        "{sym} RSI {rsi}（极度恐慌）",
        "{sym} 抛压枯竭，RSI 跌至 {rsi}",
        "{sym} RSI 破 {rsi}，超卖暗示",
    ],
    "volume_spike": [
        "{sym} 成交量爆量 {mult} 倍",
        "{sym} 量能异动，放大 {mult} 倍",
        "{sym} 突然爆量 {mult} 倍，主力进场痕迹",
    ],
    "funding_extreme": [
        "{sym} 费率 {fr}%（多头拥挤）",
        "{sym} 费率异常 {fr}%，空头被挤",
        "{sym} 资金费率飙至 {fr}%",
    ],
    "near_high": [
        "{sym} 距前高仅 {dist}%",
        "{sym} 逼近关键阻力，只差 {dist}%",
    ],
    "near_low": [
        "{sym} 距前低仅 {dist}%",
        "{sym} 逼近关键支撑，只差 {dist}%",
    ],
}


# ============================================================
# 七、轨道 ID 中文映射
# ============================================================
TRACK_ID_TO_CN = {
    "track_1": "底部突破",
    "track_2": "做空",
    "track_3": "暴跌反弹",
    "track_4": "趋势回踩",
}


def translate_track_ids(text):
    """把字符串里的 track_1 / track_2 等 ID 替换成中文名。"""
    if not text:
        return text
    for tid in sorted(TRACK_ID_TO_CN.keys(), key=len, reverse=True):
        text = str(text).replace(tid, TRACK_ID_TO_CN[tid])
    return text


# ============================================================
# 八、智能提示（与参谋长解读一致，避免自相矛盾）
# ============================================================
def build_smart_tips(md, regime):
    """
    基于市场状态生成面板提示，确保与参谋长解读方向一致。
    """
    tips = []

    regime_tip = {
        "momentum_stall":   "⏸️ 动量停滞，主力按兵不动，观望为宜",
        "strong_bull":      "🟢🟢 1D+4H双多头，回调即机会",
        "weak_bull":        "🟢 1D多头但4H震荡，只做回踩或见顶",
        "weak_bull_warning":"🚨 1D多头但4H已转空，谨防做多陷阱",
        "ranging":          "⚪ 无明确趋势，区间操作，快进快出",
        "weak_bear":        "🔴 1D空头，反弹即做空机会",
        "strong_bear":      "🔴🔴 1D+4H双空头，反弹即陷阱",
        "error":            "❗ 策略异常，本轮仅供参考",
    }.get(regime, "⚪ 状态未知，观望")
    tips.append(regime_tip)

    rsi_1h = md.get("rsi_1h")
    adx = md.get("adx")
    fp = md.get("funding_percentile")

    if rsi_1h is not None:
        if rsi_1h > 70:
            tips.append(f"1H RSI {rsi_1h:.1f} 已超买")
        elif rsi_1h < 35:
            tips.append(f"1H RSI {rsi_1h:.1f} 已超卖")

    if adx is not None:
        if adx < 18:
            tips.append(f"ADX {adx:.1f} 低位，趋势弱")
        elif adx > 25:
            tips.append(f"ADX {adx:.1f} 趋势强")

    if fp is not None and fp > 0.7:
        tips.append(f"费率拥挤度 {fp:.0%} 偏高")

    return tips


# ============================================================
# 九、工具函数
# ============================================================
def fill(template, **kwargs):
    """安全填充占位符。缺失字段时保留原样，不抛异常。"""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def pick_unique(pool, history, k=1):
    if not pool:
        return []
    candidates = list(pool)
    random.shuffle(candidates)
    picked = []
    for c in candidates:
        if c not in history:
            picked.append(c)
            if len(picked) >= k:
                return picked
    while len(picked) < k:
        picked.append(random.choice(pool))
    return picked


def pick_one(pool):
    return random.choice(pool) if pool else ""
