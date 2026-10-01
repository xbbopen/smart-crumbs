# -*- coding: utf-8 -*-
"""
v1_default 策略 - 整改版 v2
=========================
P0 修复：
1. 轨道3 极端波动熔断（波动率飙升时禁止抄底，避免在插针中开仓）
2. 止损双保险改为分层目标风险（BTC/ETH 2%，中市值 2.5%，altcoin 3%）

P1 修复：
3. CVD 改用 OBV（基于价格变化累加成交量，比阴阳线估算更稳健）
4. 动量停滞阈值改为 ATR 归一化（跨币种一致）

v1 原有：
- 4H 趋势判断统一为 momentum_4h
- 轨道3 完全独立，触发条件 4/4 全满足
- 新增 KDJ 极端值过滤（J>100 否决做多）
- 轨道4 加"站上30m MA10"硬条件
- 生成 global_judgment 供报告层统一使用
"""
from strategies.base import BaseStrategy


MAJOR_COINS = {"BTC", "ETH"}
MID_COINS = {"SOL", "BNB", "XRP", "ADA", "AVAX", "LINK", "DOGE"}

# 🚀 P0: 按分层设定目标止损空间
TARGET_RISK_BY_TIER = {
    "major": 0.020,   # BTC/ETH: 2.0%
    "mid": 0.025,     # 中市值: 2.5%
    "alt": 0.030,     # altcoin: 3.0%
}


def get_tier(symbol):
    """按币种分层返回 tier 名称。"""
    sym = (symbol or "").replace("_USDT", "").replace("_usdt", "").upper()
    if sym in MAJOR_COINS:
        return "major"
    elif sym in MID_COINS:
        return "mid"
    return "alt"


def get_atr_multiplier(symbol):
    """保留旧的 ATR 倍数函数，供兼容使用。"""
    sym = (symbol or "").replace("_USDT", "").replace("_usdt", "").upper()
    if sym in MAJOR_COINS:
        return 1.5
    elif sym in MID_COINS:
        return 2.0
    return 2.5


class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长多周期共振策略"

    # ============ 4H 动能评分 ============
    def _calc_4h_momentum(self, md):
        ema20 = md.get("ema20_4h"); ema50 = md.get("ema50_4h")
        rsi_4h = md.get("rsi_4h"); macd_4h = md.get("macd_4h") or {}
        price = md.get("current_price")
        score = 0
        if ema20 and ema50: score += 1 if ema20 > ema50 else -1
        if ema20 and price: score += 1 if price > ema20 else -1
        if rsi_4h is not None:
            if rsi_4h > 55: score += 1
            elif rsi_4h < 45: score -= 1
        if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None:
            score += 1 if macd_4h["dif"] > macd_4h["dea"] else -1
        if score >= 3: return "strong_bull"
        elif score >= 1: return "bull"
        elif score <= -3: return "strong_bear"
        elif score <= -1: return "bear"
        return "neutral"

    # ============ 🚀 P1: 动量停滞改为 ATR 归一化 ============
    def _detect_momentum_stall(self, md):
        """动量停滞：MACD 柱相对 ATR 极小 + ADX 低位。"""
        macd_4h = md.get("macd_4h") or {}
        adx = md.get("adx")
        atr = md.get("atr")
        hist = macd_4h.get("hist")
        price = md.get("current_price")

        if hist is None or adx is None or atr is None or not price or price <= 0:
            return False
        try:
            if float(atr) <= 0:
                return False
            # 🚀 P1: 用 ATR 归一化，跨币种一致
            return abs(float(hist)) / float(atr) < 0.3 and adx < 20
        except (TypeError, ValueError):
            return False

    # ============ 波动率熔断 ============
    def _detect_volatility_spike(self, md):
        atr = md.get("atr"); price = md.get("current_price")
        if not atr or not price or price <= 0:
            return False
        try:
            return (float(atr) / float(price)) > 0.03
        except (TypeError, ValueError):
            return False

    # ============ 🚀 P1: OBV 计算（替代 CVD） ============
    def _calc_obv_series(self, klines):
        """
        OBV 累积量：价涨加量，价跌减量，价平不加不减。
        比基于阴阳线估算的 CVD 更稳健，避免震荡市的噪声误报。
        """
        if not klines:
            return []
        obv_series = [0]
        obv = 0
        for i in range(1, len(klines)):
            if klines[i]["close"] > klines[i - 1]["close"]:
                obv += klines[i]["volume"]
            elif klines[i]["close"] < klines[i - 1]["close"]:
                obv -= klines[i]["volume"]
            obv_series.append(obv)
        return obv_series

    # ============ 市场状态机 ============
    def _determine_regime(self, md):
        """返回 (regime, allowed_tracks, forbidden_tracks, description)
        注意：track_3 完全独立，不出现在 allowed/forbidden 里
        """
        if self._detect_volatility_spike(md):
            return ("volatile", [], ["track_1", "track_2", "track_4"],
                    "⚡ 极端波动，所有轨道暂停，等市场平静")
        if self._detect_momentum_stall(md):
            return ("momentum_stall", [], ["track_1", "track_2", "track_4"],
                    "⏸️ 动量停滞，主力按兵不动，观望为宜")

        trend_1d = md.get("trend_1d")
        momentum_4h = self._calc_4h_momentum(md)

        if trend_1d == "down" and momentum_4h in ("bear", "strong_bear"):
            return ("strong_bear", ["track_2"], ["track_1", "track_4"],
                    "🔴🔴 1D+4H双空头，只允许做空")
        if trend_1d == "down":
            return ("weak_bear", ["track_2"], ["track_1", "track_4"],
                    "🔴 1D空头，只允许做空")
        if trend_1d == "up" and momentum_4h in ("bull", "strong_bull"):
            return ("strong_bull", ["track_1", "track_4"], ["track_2"],
                    "🟢🟢 1D+4H双多头，只允许做多")
        if trend_1d == "up":
            if momentum_4h in ("bear", "strong_bear"):
                return ("weak_bull_warning", ["track_2"], ["track_1", "track_4"],
                        "🚨 1D多头但4H已转空，谨防做多陷阱")
            return ("weak_bull", ["track_4", "track_2"], ["track_1"],
                    "🟡 1D多头但4H震荡，只允许回踩做多或见顶做空")
        return ("ranging", ["track_2"], ["track_1", "track_4"],
                "⚪ 无明确趋势，只允许做空")

    # ============ 🚀 P0: 分档入场计划（止损分层） ============
    def _build_entry_plan(self, symbol, direction, cp, atr, rh, rl, ma10, boll_mid, trigger_type):
        tier = get_tier(symbol)
        base_risk = TARGET_RISK_BY_TIER[tier]
        stages = []; note = ""

        if trigger_type == "long_trend":
            if atr:
                stages.append({"weight": 40, "type": "limit", "price": cp - 0.5 * atr, "note": f"限价回踩 {cp - 0.5*atr:.4f}"})
                stages.append({"weight": 35, "type": "limit", "price": cp - 1.0 * atr, "note": f"限价回踩 {cp - 1.0*atr:.4f}"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.995, "note": "限价 -0.5%"})
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.99, "note": "限价 -1%"})
            if rl:
                stages.append({"weight": 25, "type": "limit", "price": rl * 1.005, "note": "前低上方"})
            else:
                stages.append({"weight": 25, "type": "limit", "price": cp * 0.98, "note": "限价 -2%"})
            note = "底部突破后往往有回踩，全部限价接飞刀"
        elif trigger_type == "short_reversal":
            stages.append({"weight": 70, "type": "market", "price": cp, "note": "顶部窗口极短，立即市价"})
            if rh:
                stages.append({"weight": 30, "type": "limit", "price": rh * 1.01, "note": "前高上方加仓"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 1.02, "note": "反弹 +2%"})
            note = "见顶反转窗口极短，首档必须市价"
        elif trigger_type == "short_trend_follow":
            if boll_mid and boll_mid >= cp:
                stages.append({"weight": 60, "type": "limit", "price": boll_mid, "note": "限价 1H 布林中轨"})
                stages.append({"weight": 40, "type": "limit", "price": boll_mid * 1.015, "note": "中轨上方 1.5%"})
            elif ma10 and ma10 >= cp:
                stages.append({"weight": 60, "type": "limit", "price": ma10, "note": "限价 30m MA10"})
                stages.append({"weight": 40, "type": "limit", "price": ma10 * 1.02, "note": "MA10 上方 2%"})
            else:
                stages.append({"weight": 60, "type": "limit", "price": cp * 1.01, "note": "限价 +1%"})
                stages.append({"weight": 40, "type": "limit", "price": cp * 1.02, "note": "限价 +2%"})
            note = "下跌中继反弹很磨人，等反弹到阻力位挂限价空单"
        elif trigger_type == "long_rebound":
            if atr:
                stages.append({"weight": 35, "type": "limit", "price": cp - 0.5 * atr, "note": "-0.5ATR"})
                stages.append({"weight": 35, "type": "limit", "price": cp - 1.5 * atr, "note": "-1.5ATR"})
            else:
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.99, "note": "-1%"})
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.97, "note": "-3%"})
            if rl:
                stages.append({"weight": 30, "type": "limit", "price": rl * 0.99, "note": "前低下方"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 0.95, "note": "-5%"})
            note = "暴跌反弹是稀有事件，全部限价，避免抄在半山腰"
        elif trigger_type == "long_pullback":
            stages.append({"weight": 60, "type": "market", "price": cp, "note": "已到回踩位，市价占位"})
            if boll_mid:
                stages.append({"weight": 40, "type": "limit", "price": boll_mid * 0.99, "note": "中轨下方 1%"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.98, "note": "限价 -2%"})
            note = "趋势中回踩到位即入场"

        total_weight = sum(s["weight"] for s in stages)
        avg_price = sum(s["price"] * s["weight"] for s in stages) / total_weight if total_weight > 0 else cp

        # 🚀 P0: 止损分层 + ATR 微调
        is_long = direction.startswith("long")
        if atr and avg_price > 0:
            atr_pct = atr / avg_price
            # 目标止损 = max(分层基础值, 1.2 倍 ATR%)
            risk_pct = max(base_risk, atr_pct * 1.2)
            # 上限：分层基础值的 1.5 倍（避免 ATR 过大时止损过宽）
            risk_pct = min(risk_pct, base_risk * 1.5)
        else:
            risk_pct = base_risk

        stop = avg_price * (1 - risk_pct) if is_long else avg_price * (1 + risk_pct)
        note += f"（目标止损 {risk_pct*100:.2f}%）"

        return {"stages": stages, "avg_price": avg_price, "stop": stop, "note": note}

    # ============ 🚀 P0: 轨道3 独立评估（加波动率熔断） ============
    def _evaluate_track3_standalone(self, md):
        # 🚀 P0: 极端波动熔断，禁止抄底
        if self._detect_volatility_spike(md):
            return {
                "score": 0, "max": 4,
                "details": {"⛔ 极端波动熔断": "波动率飙升（ATR/价格 > 3%），禁止抄底"},
                "hard_ok": False, "allowed": False,
                "independent": True, "triggered": False,
            }

        price = md.get("current_price"); rh = md.get("recent_high")
        rsi = md.get("rsi"); kdj_1h = md.get("kdj_1h") or {}
        klines = md.get("klines_30m") or []

        t3 = {"score": 0, "max": 4, "details": {}, "hard_ok": True,
              "allowed": True, "independent": True, "triggered": False}

        if price and rh and rh > 0:
            dd = (rh - price) / rh * 100
            if dd >= 20:
                t3["score"] += 1
                t3["details"]["1.大幅回撤"] = f"✅ 从高点回撤 {dd:.1f}%（≥20%）"
            else:
                t3["details"]["1.大幅回撤"] = f"❌ 从高点回撤 {dd:.1f}%（需≥20%）"
        else:
            t3["details"]["1.大幅回撤"] = "❌ 数据缺失"

        if rsi is not None and rsi <= 25:
            t3["score"] += 1
            t3["details"]["2.30m RSI 极端超卖"] = f"✅ RSI={rsi:.1f}（≤25）"
        else:
            rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
            t3["details"]["2.30m RSI 极端超卖"] = f"❌ RSI={rsi_str}（需≤25）"

        kdj_j = kdj_1h.get("j")
        if kdj_j is not None and kdj_j < -15:
            t3["score"] += 1
            t3["details"]["3.1H KDJ 极度超卖"] = f"✅ J={kdj_j:.1f}（<-15）"
        else:
            j_str = f"{kdj_j:.1f}" if kdj_j is not None else "N/A"
            t3["details"]["3.1H KDJ 极度超卖"] = f"❌ J={j_str}（需<-15）"

        if klines and len(klines) >= 2:
            last = klines[-1]; prev = klines[-2]
            body = abs(last["close"] - last["open"])
            lower_shadow = min(last["close"], last["open"]) - last["low"]
            is_hammer = body > 0 and lower_shadow > body * 2.0
            is_engulf = (prev["close"] < prev["open"] and last["close"] > last["open"]
                         and last["open"] <= prev["close"] and last["close"] >= prev["open"])
            if is_hammer:
                t3["score"] += 1
                t3["details"]["4.30m 止跌形态"] = "✅ 锤头线"
            elif is_engulf:
                t3["score"] += 1
                t3["details"]["4.30m 止跌形态"] = "✅ 看涨吞没"
            else:
                t3["details"]["4.30m 止跌形态"] = "❌ 未见止跌形态"
        else:
            t3["details"]["4.30m 止跌形态"] = "❌ 数据不足"

        t3["triggered"] = (t3["score"] == 4)
        return t3

    # ============ 主评估 ============
    def evaluate(self, symbol, asset_type, market_data):
        is_spot_mode = market_data.get("data_mode") == "spot"
        price = market_data.get("current_price"); rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10"); rh = market_data.get("recent_high")
        rl = market_data.get("recent_low"); adx = market_data.get("adx")
        klines = market_data.get("klines_30m", []); fp = market_data.get("funding_percentile")
        trend_1d = market_data.get("trend_1d"); rsi_1h = market_data.get("rsi_1h")
        kdj_1h = market_data.get("kdj_1h") or {}; boll_1h = market_data.get("boll_1h") or {}
        macd_1h = market_data.get("macd_1h") or {}; macd_4h = market_data.get("macd_4h") or {}
        rsi_div = market_data.get("rsi_div_1h"); atr = market_data.get("atr")
        boll_mid = boll_1h.get("mid"); ema20_4h = market_data.get("ema20_4h")
        ema50_4h = market_data.get("ema50_4h")

        regime, allowed, forbidden, regime_desc = self._determine_regime(market_data)
        momentum_4h = self._calc_4h_momentum(market_data)
        atr_pct = (atr / price * 100) if (atr and price) else 0

        result = {
            "regime": regime, "regime_desc": regime_desc,
            "momentum_4h": momentum_4h, "atr_pct": round(atr_pct, 2),
            "allowed_tracks": allowed, "forbidden_tracks": forbidden,
            "track_1": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "allowed": "track_1" in allowed},
            "track_2": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "allowed": "track_2" in allowed, "sub_type": None},
            "track_3": {"score": 0, "max": 4, "details": {}, "hard_ok": False, "allowed": True, "independent": True},
            "track_4": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "allowed": "track_4" in allowed},
            "triggered": False, "direction": None, "reason": "",
            "entry_plan": None,
            "data_mode": "spot" if is_spot_mode else "futures",
        }

        if price is None or rsi is None or rh is None or rl is None:
            result["track_1"]["details"]["数据不足"] = "❌ 核心指标缺失"
            return result

        rsi_str = f"{rsi:.1f}"
        rsi_1h_str = f"{rsi_1h:.1f}" if rsi_1h is not None else "N/A"
        kdj_j_str = f"{kdj_1h['j']:.1f}" if kdj_1h.get("j") is not None else "N/A"

        adx_ok_1 = adx is not None and adx >= 22
        adx_ok_2 = adx is not None and adx >= 20
        adx_ok_4 = adx is not None and adx >= 18

        # 🚀 P1: 用 OBV 替代 CVD
        obv_series = self._calc_obv_series(klines)

        # ============ 轨道1 ============
        if regime in ("volatile", "momentum_stall"):
            result["track_1"]["details"]["⛔ 状态熔断"] = regime_desc
        elif not result["track_1"]["allowed"]:
            result["track_1"]["details"]["❌ 状态禁止"] = "当前状态禁止此轨道"
        elif rh and (rh - price) / rh < 0.10:
            result["track_1"]["details"]["硬条件"] = f"❌ 距高点不足10%（当前{((rh-price)/rh*100):.1f}%）"
        elif trend_1d == "down":
            result["track_1"]["details"]["硬条件"] = "❌ 1D趋势向下"
        elif not adx_ok_1:
            result["track_1"]["details"]["硬条件"] = f"❌ ADX<22"
        else:
            result["track_1"]["hard_ok"] = True
            result["track_1"]["details"]["硬条件"] = "✅ 1D多头 + 距高点≥10% + ADX≥22"
            if price <= rl * 1.05:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.底部区域"] = f"✅ 现价接近前低 {rl}"
            else:
                result["track_1"]["details"]["1.底部区域"] = f"❌ 高于前低1.05倍"
            if ma10 and price > ma10:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["2.站上30m MA10"] = f"✅ {price} > {ma10:.4f}"
            else:
                result["track_1"]["details"]["2.站上30m MA10"] = f"❌ 未站上"
            if 45 <= rsi <= 65 or (rsi_1h is not None and rsi_1h < 40):
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["3.30m RSI温和"] = f"✅ RSI={rsi_str}"
            else:
                result["track_1"]["details"]["3.30m RSI温和"] = f"❌ RSI={rsi_str}"
            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["volume"] > avg_vol * 1.3 and klines[-1]["close"] > klines[-1]["open"]:
                    result["track_1"]["score"] += 1
                    result["track_1"]["details"]["4.30m放量阳线"] = "✅ 放量"
                else:
                    result["track_1"]["details"]["4.30m放量阳线"] = "❌ 未见放量"
            if kdj_1h.get("j") is not None and kdj_1h["j"] < 20:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"✅ J={kdj_j_str}"
            else:
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_j_str}"
            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] > macd_4h["dea"]:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["6.4H MACD金叉"] = "✅ DIF>DEA"
            else:
                result["track_1"]["details"]["6.4H MACD金叉"] = "❌ 未金叉"

        # ============ 轨道2 ============
        if regime in ("volatile", "momentum_stall"):
            result["track_2"]["details"]["⛔ 状态熔断"] = regime_desc
        elif not result["track_2"]["allowed"]:
            result["track_2"]["details"]["❌ 状态禁止"] = "当前状态禁止做空"
        elif momentum_4h in ("bull", "strong_bull"):
            result["track_2"]["details"]["硬条件"] = "❌ 4H动能向上，禁止做空"
        elif trend_1d == "up" and momentum_4h == "neutral":
            result["track_2"]["details"]["硬条件"] = "❌ 日线多头+4H震荡，做空需4H明确转空"
        elif not adx_ok_2:
            result["track_2"]["details"]["硬条件"] = f"❌ ADX<20"
        else:
            result["track_2"]["hard_ok"] = True
            result["track_2"]["details"]["硬条件"] = "✅ ADX≥20 + 4H未转多"

            # 2A 见顶做空
            result["track_2"]["details"]["──────── 📌 见顶做空（反转）────────"] = ""
            rev = 0
            if price >= rh * 0.97:
                rev += 1
                result["track_2"]["details"]["A1.逼近高点"] = f"✅ {price} 接近 {rh}"
            else:
                result["track_2"]["details"]["A1.逼近高点"] = f"❌ 距高{((rh-price)/rh*100):.1f}%"
            if rsi >= 70:
                rev += 1
                result["track_2"]["details"]["A2.30m RSI超买"] = f"✅ RSI={rsi_str}"
            else:
                result["track_2"]["details"]["A2.30m RSI超买"] = f"❌ RSI={rsi_str}"

            # 🚀 P1: OBV 顶背离（价格新高，OBV 未新高）
            if obv_series and len(obv_series) >= 10:
                high_now = max(k["high"] for k in klines[-3:])
                high_prev = max(k["high"] for k in klines[-10:-3])
                # OBV 顶背离：价格新高，但 OBV 当前值未超过 4 根前的值
                if high_now > high_prev and obv_series[-1] <= obv_series[-4]:
                    rev += 1
                    result["track_2"]["details"]["A3.30m OBV顶背离"] = "✅ 价新高OBV未新高"
                else:
                    result["track_2"]["details"]["A3.30m OBV顶背离"] = "❌ 未出现"
            else:
                result["track_2"]["details"]["A3.30m OBV顶背离"] = "❌ 数据不足"

            a4_reasons = []
            if rsi_1h is not None and rsi_1h >= 70: a4_reasons.append(f"1h RSI={rsi_1h_str}")
            if rsi_div == "bearish": a4_reasons.append("1h顶背离")
            if fp is not None and fp > 0.5: a4_reasons.append(f"费率拥挤={fp:.1%}")
            if a4_reasons:
                rev += 1
                result["track_2"]["details"]["A4.1H过热/背离/费率"] = f"✅ {' | '.join(a4_reasons)}"
            else:
                result["track_2"]["details"]["A4.1H过热/背离/费率"] = f"❌ 1h RSI={rsi_1h_str}"
            if kdj_1h.get("j") is not None and kdj_1h["j"] > 100:
                rev += 1
                result["track_2"]["details"]["A5.1H KDJ超买"] = f"✅ J={kdj_j_str}"
            else:
                result["track_2"]["details"]["A5.1H KDJ超买"] = f"❌ J={kdj_j_str}"
            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] < macd_4h["dea"]:
                rev += 1
                result["track_2"]["details"]["A6.4H MACD死叉"] = "✅ DIF<DEA"
            else:
                result["track_2"]["details"]["A6.4H MACD死叉"] = "❌ 未死叉"
            result["track_2"]["details"]["📊 见顶做空得分"] = f"{rev}/6"

            # 2B 顺势做空
            result["track_2"]["details"]["──────── 📉 顺势做空（延续）────────"] = ""
            tf_core = 0; tf_aux = 0
            if ema20_4h and ema50_4h and ema20_4h < ema50_4h:
                tf_core += 1
                result["track_2"]["details"]["B1.4H空头结构"] = f"✅ EMA20={ema20_4h:.4f} < EMA50={ema50_4h:.4f}"
            else:
                if ema20_4h and ema50_4h:
                    result["track_2"]["details"]["B1.4H空头结构"] = f"❌ 未形成（EMA20={ema20_4h:.4f} > EMA50={ema50_4h:.4f}）"
                else:
                    result["track_2"]["details"]["B1.4H空头结构"] = "❌ 数据不足（缺少 EMA20 或 EMA50）"
            if boll_mid:
                dist = (price - boll_mid) / boll_mid
                if dist >= -0.005:
                    tf_core += 1
                    result["track_2"]["details"]["B2.接近中轨"] = f"✅ 距离{dist*100:+.1f}%"
                else:
                    result["track_2"]["details"]["B2.接近中轨"] = f"❌ 已跌破中轨"
            if rsi_1h is not None and 40 <= rsi_1h <= 60:
                tf_aux += 1
                result["track_2"]["details"]["B3.1H RSI中位"] = f"✅ RSI={rsi_1h_str}"
            else:
                result["track_2"]["details"]["B3.1H RSI中位"] = f"❌ RSI={rsi_1h_str}"
            if klines and len(klines) >= 2:
                last, prev = klines[-1], klines[-2]
                body = abs(last["close"] - last["open"])
                upper = last["high"] - max(last["close"], last["open"])
                bear_engulf = (prev["close"] > prev["open"] and last["close"] < last["open"]
                               and last["open"] >= prev["close"] and last["close"] <= prev["open"])
                if body > 0 and upper > body * 1.2:
                    tf_aux += 1
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "✅ 长上影"
                elif bear_engulf:
                    tf_aux += 1
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "✅ 看跌吞没"
                else:
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "❌ 未见"
            if macd_1h.get("dif") is not None and macd_1h.get("dea") is not None and macd_1h["dif"] < macd_1h["dea"]:
                tf_aux += 1
                result["track_2"]["details"]["B5.1H MACD空头"] = f"✅ DIF={macd_1h['dif']:.4f} < DEA={macd_1h['dea']:.4f}"
            else:
                result["track_2"]["details"]["B5.1H MACD空头"] = "❌ 1H MACD未死叉"
            if rsi < 60:
                tf_aux += 1
                result["track_2"]["details"]["B6.30m RSI未超买"] = f"✅ RSI={rsi_str}"
            else:
                result["track_2"]["details"]["B6.30m RSI未超买"] = f"❌ RSI={rsi_str}"
            result["track_2"]["details"]["📊 顺势做空核心"] = f"{tf_core}/2"
            result["track_2"]["details"]["📊 顺势做空辅助"] = f"{tf_aux}/4"

            if rev >= 4:
                result["track_2"]["sub_type"] = "reversal"
                result["track_2"]["score"] = rev
                result["track_2"]["reason"] = f"见顶做空 {rev}/6"
            elif tf_core >= 2 and tf_aux >= 2:
                result["track_2"]["sub_type"] = "trend_follow"
                result["track_2"]["score"] = tf_core + tf_aux
                result["track_2"]["reason"] = f"顺势做空 核心{tf_core}/2+辅助{tf_aux}/4"

        # ============ 轨道3（独立，P0 熔断） ============
        result["track_3"] = self._evaluate_track3_standalone(market_data)

        # ============ 轨道4 ============
        if regime in ("volatile", "momentum_stall"):
            result["track_4"]["details"]["⛔ 状态熔断"] = regime_desc
        elif not result["track_4"]["allowed"]:
            result["track_4"]["details"]["❌ 状态禁止"] = "当前状态禁止此轨道"
        elif trend_1d != "up":
            result["track_4"]["details"]["硬条件"] = "❌ 需1D多头"
        elif momentum_4h not in ("bull", "strong_bull"):
            result["track_4"]["details"]["硬条件"] = "❌ 需4H动能多头"
        elif not adx_ok_4:
            result["track_4"]["details"]["硬条件"] = "❌ ADX<18"
        elif ma10 and price < ma10 * 0.995:
            result["track_4"]["details"]["硬条件"] = f"❌ 价格未站上MA10（{price}<{ma10:.4f}）"
        elif kdj_1h.get("j") is not None and kdj_1h["j"] > 100:
            result["track_4"]["details"]["硬条件"] = f"❌ 1H KDJ极度超买 J={kdj_j_str}，做多风险极高"
        else:
            result["track_4"]["hard_ok"] = True
            result["track_4"]["details"]["硬条件"] = "✅ 1D多头 + 4H动能多头 + ADX≥18 + 站上MA10 + KDJ未超买"

            tolerance = 0.02
            if atr and price and price > 0:
                atr_pct_val = atr / price
                tolerance = max(0.01, min(atr_pct_val * 1.5, 0.04))
            if boll_mid and abs(price - boll_mid) / boll_mid <= tolerance:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["1.回踩布林中轨"] = f"✅ 距中轨{(price-boll_mid)/boll_mid*100:+.2f}%（容差±{tolerance*100:.1f}%）"
            else:
                result["track_4"]["details"]["1.回踩布林中轨"] = f"❌ 距中轨较远"
            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["volume"] < avg_vol * 0.8:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["2.缩量回踩"] = "✅ 缩量"
                else:
                    result["track_4"]["details"]["2.缩量回踩"] = "❌ 未缩量"
            if klines and len(klines) >= 2:
                last = klines[-1]
                body = abs(last["close"] - last["open"])
                lower = min(last["close"], last["open"]) - last["low"]
                if body > 0 and lower > body * 1.2:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["3.30m止跌形态"] = "✅ 长下影"
                else:
                    result["track_4"]["details"]["3.30m止跌形态"] = "❌ 未见"
            if rsi_1h is not None and 40 <= rsi_1h <= 55:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["4.1H RSI健康"] = f"✅ RSI={rsi_1h_str}"
            else:
                result["track_4"]["details"]["4.1H RSI健康"] = f"❌ RSI={rsi_1h_str}"
            if ma10 and price > ma10:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["5.站上30m MA10"] = f"✅ {price} > {ma10:.4f}"
            else:
                result["track_4"]["details"]["5.站上30m MA10"] = f"❌ 未站上"
            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] >= macd_4h["dea"]:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["6.4H MACD健康"] = "✅ DIF>=DEA"
            else:
                result["track_4"]["details"]["6.4H MACD健康"] = "❌ 已死叉"

        # ============ 最终裁决 ============
        candidates = []
        if result["track_3"].get("triggered"):
            candidates.append(("long_rebound", "track_3", 4, "暴跌反弹 4/4（独立轨道）"))
        if result["track_2"]["hard_ok"] and result["track_2"].get("sub_type"):
            candidates.append(("short", "track_2", result["track_2"]["score"], result["track_2"]["reason"]))
        if result["track_4"]["hard_ok"] and result["track_4"]["score"] >= 4:
            candidates.append(("long_pullback", "track_4", result["track_4"]["score"], f"趋势回踩 {result['track_4']['score']}/6"))
        if result["track_1"]["hard_ok"] and result["track_1"]["score"] >= 4:
            candidates.append(("long_trend", "track_1", result["track_1"]["score"], f"底部突破 {result['track_1']['score']}/6"))

        if candidates:
            priority = {"long_rebound": 5, "short": 4, "long_pullback": 3, "long_trend": 1}
            candidates.sort(key=lambda x: (priority.get(x[0], 0), x[2]), reverse=True)
            winner_dir, winner_track, _, winner_reason = candidates[0]
            result["triggered"] = True
            result["direction"] = winner_dir
            result["reason"] = winner_reason
            if len(candidates) > 1:
                losers = [f"{c[1]}({c[3]})" for c in candidates[1:]]
                result["conflict_note"] = f"⚠️ 多轨道冲突！优先 [{winner_track}]，被否决：{', '.join(losers)}"
            tt_map = {
                "short": "short_trend_follow" if result["track_2"]["sub_type"] == "trend_follow" else "short_reversal",
                "long_pullback": "long_pullback",
                "long_rebound": "long_rebound",
                "long_trend": "long_trend",
            }
            result["entry_plan"] = self._build_entry_plan(
                symbol, winner_dir, price, atr, rh, rl, ma10, boll_mid, tt_map.get(winner_dir, "long_trend")
            )
            if is_spot_mode and winner_dir == "short":
                result["direction"] = "spot_warning"
                result["reason"] = "现货逃顶预警（" + winner_reason + "）"
                result["entry_plan"] = None

        # ============ global_judgment ============
        warnings = []
        if kdj_1h.get("j") is not None and kdj_1h["j"] > 100:
            warnings.append(f"1H KDJ 极度超买 J={kdj_j_str}")
        if atr_pct > 2.0:
            warnings.append(f"波动率偏高 ATR={atr_pct:.1f}%")

        result["global_judgment"] = {
            "regime": regime,
            "momentum_4h": momentum_4h,
            "atr_pct": round(atr_pct, 2),
            "volatility_level": "volatile" if regime == "volatile" else "normal",
            "warnings": warnings,
        }

        return result
