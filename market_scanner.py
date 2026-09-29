# -*- coding: utf-8 -*-
"""
市场热点扫描器。
遍历所有标的的 market_data，输出当期热点摘要，供报告顶部渲染。
全部用 try/except 兜底：任何异常都返回空热点，不影响主流程。
"""
import logging

from report_lexicon import SESSION_MAP, judge_session

log = logging.getLogger(__name__)

# 扫描参数（可调）
LOOKBACK_BARS = 40          # 涨跌幅回看 40 根 30m = 20 小时
RSI_GREEDY = 70             # RSI 超买阈值
RSI_PANIC = 30              # RSI 超卖阈值
VOL_SPIKE_MULT = 2.0        # 成交量爆量倍数
FUNDING_EXTREME = 0.01      # 资金费率极端阈值（%）
NEAR_HIGH_PCT = 3.0         # 距前高 3% 内视为"逼近"
NEAR_LOW_PCT = 3.0          # 距前低 3% 内视为"逼近"
TOP_N = 3                   # 每个榜单取 Top N


def _empty_hotspot():
    return {
        "session": "unknown",
        "session_label": "",
        "session_vibe": "",
        "top_gainers": [],
        "top_losers": [],
        "extreme_greed": [],
        "extreme_fear": [],
        "volume_spikes": [],
        "near_highs": [],
        "near_lows": [],
        "funding_extreme": [],
    }


def scan_market(all_results, hour_bjt: int = None):
    """
    扫描所有标的，输出热点摘要。
    hour_bjt：北京时间的小时数，None 则内部用 datetime 计算。
    """
    hotspot = _empty_hotspot()
    try:
        from datetime import datetime
        from report_builder import BJT  # 复用时报时区

        if hour_bjt is None:
            hour_bjt = datetime.now(BJT).hour

        session_key = judge_session(hour_bjt)
        session_info = SESSION_MAP.get(session_key, SESSION_MAP["asia_morning"])
        hotspot["session"] = session_key
        hotspot["session_label"] = session_info["label"]
        hotspot["session_vibe"] = session_info["vibe"]

        gainers, losers = [], []
        greed, fear = [], []
        vol_spikes = []
        near_high, near_low = [], []
        funding_ext = []

        for r in all_results:
            if r.get("status") != "ok":
                continue
            md = r.get("market_data") or {}
            klines = md.get("klines_30m") or []
            sym = r["symbol"].replace("_USDT", "")

            # ---- 涨跌幅 ----
            if len(klines) >= LOOKBACK_BARS:
                old_close = klines[-LOOKBACK_BARS]["close"]
                new_close = klines[-1]["close"]
                if old_close and old_close > 0:
                    pct = (new_close - old_close) / old_close * 100
                    if pct > 0:
                        gainers.append({"sym": sym, "pct": pct})
                    else:
                        losers.append({"sym": sym, "pct": pct})

            # ---- RSI 极端 ----
            rsi = md.get("rsi")
            if rsi is not None:
                if rsi >= RSI_GREEDY:
                    greed.append({"sym": sym, "rsi": rsi})
                elif rsi <= RSI_PANIC:
                    fear.append({"sym": sym, "rsi": rsi})

            # ---- 成交量异动 ----
            if len(klines) >= 21:
                recent = klines[-1]["volume"]
                avg = sum(k["volume"] for k in klines[-21:-1]) / 20
                if avg > 0 and recent > avg * VOL_SPIKE_MULT:
                    vol_spikes.append({"sym": sym, "mult": recent / avg})

            # ---- 资金费率极端 ----
            fr = md.get("funding_rate")
            if fr is not None and abs(fr) >= FUNDING_EXTREME:
                funding_ext.append({"sym": sym, "fr": fr})

            # ---- 逼近高低点 ----
            cp = md.get("current_price")
            rh = md.get("recent_high")
            rl = md.get("recent_low")
            if cp and rh and rh > 0:
                dist = (rh - cp) / rh * 100
                if 0 <= dist <= NEAR_HIGH_PCT:
                    near_high.append({"sym": sym, "dist": dist})
            if cp and rl and rl > 0:
                dist = (cp - rl) / rl * 100
                if 0 <= dist <= NEAR_LOW_PCT:
                    near_low.append({"sym": sym, "dist": dist})

        # 排序 + 截断
        gainers.sort(key=lambda x: x["pct"], reverse=True)
        losers.sort(key=lambda x: x["pct"])
        greed.sort(key=lambda x: x["rsi"], reverse=True)
        fear.sort(key=lambda x: x["rsi"])
        vol_spikes.sort(key=lambda x: x["mult"], reverse=True)
        funding_ext.sort(key=lambda x: abs(x["fr"]), reverse=True)
        near_high.sort(key=lambda x: x["dist"])
        near_low.sort(key=lambda x: x["dist"])

        hotspot["top_gainers"] = gainers[:TOP_N]
        hotspot["top_losers"] = losers[:TOP_N]
        hotspot["extreme_greed"] = greed[:TOP_N]
        hotspot["extreme_fear"] = fear[:TOP_N]
        hotspot["volume_spikes"] = vol_spikes[:TOP_N]
        hotspot["funding_extreme"] = funding_ext[:TOP_N]
        hotspot["near_highs"] = near_high[:TOP_N]
        hotspot["near_lows"] = near_low[:TOP_N]

    except Exception as e:
        log.error(f"[market_scanner] 扫描异常：{type(e).__name__}: {e}")
        return _empty_hotspot()

    return hotspot
