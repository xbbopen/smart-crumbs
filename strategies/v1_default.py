from strategies.base import BaseStrategy


class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长多周期共振策略"

    # ================= 4H 动能细分 =================
    def _calc_4h_momentum(self, md):
        """
        综合判断4H动能，返回：strong_bull / bull / neutral / bear / strong_bear
        比单纯看EMA20/EMA50更敏感
        """
        ema20 = md.get("ema20_4h")
        ema50 = md.get("ema50_4h")
        rsi_4h = md.get("rsi_4h")
        macd_4h = md.get("macd_4h") or {}
        price = md.get("current_price")

        score = 0
        # 1. EMA结构
        if ema20 and ema50:
            score += 1 if ema20 > ema50 else -1
        # 2. 价格位置
        if ema20 and price:
            score += 1 if price > ema20 else -1
        # 3. RSI
        if rsi_4h is not None:
            if rsi_4h > 55: score += 1
            elif rsi_4h < 45: score -= 1
        # 4. MACD
        if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None:
            score += 1 if macd_4h["dif"] > macd_4h["dea"] else -1

        if score >= 3: return "strong_bull"
        elif score >= 1: return "bull"
        elif score <= -3: return "strong_bear"
        elif score <= -1: return "bear"
        else: return "neutral"

    # ================= 市场状态机 =================
    def _determine_regime(self, md):
        """
        返回 (regime, allowed_tracks, forbidden_tracks, description)
        regime 五态：
        - strong_bear: 1D空 + 4H空 → 只允许做空
        - weak_bear:   1D空 + 4H非空 → 允许做空，禁止做多
        - strong_bull: 1D多 + 4H多 → 只允许做多
        - weak_bull:   1D多 + 4H非多 → 做多需谨慎，禁止无脑抄底
        - ranging:     1D震荡 + 4H震荡 → 短线双向，但加严条件
        """
        trend_1d = md.get("trend_1d")
        momentum_4h = self._calc_4h_momentum(md)

        # 1D空 + 4H空
        if trend_1d == "down" and momentum_4h in ("bear", "strong_bear"):
            return ("strong_bear",
                    ["track_2"],
                    ["track_1", "track_3", "track_4"],
                    "1D+4H双空头，只允许做空，禁止任何做多")

        # 1D空 + 4H非空
        if trend_1d == "down":
            return ("weak_bear",
                    ["track_2"],
                    ["track_1", "track_4"],
                    "1D空头，允许做空，禁止做多（轨道3有条件允许短线反弹）")

        # 1D多 + 4H多
        if trend_1d == "up" and momentum_4h in ("bull", "strong_bull"):
            return ("strong_bull",
                    ["track_1", "track_4"],
                    ["track_2", "track_3"],
                    "1D+4H双多头，只允许做多，禁止任何做空")

        # 1D多 + 4H非多（你截图里NEAR/ZEC的情况）
        if trend_1d == "up":
            if momentum_4h in ("bear", "strong_bear"):
                # 日线多头但4H已转空，这是最危险的陷阱区
                return ("weak_bull_warning",
                        ["track_2"],
                        ["track_1", "track_3", "track_4"],
                        "⚠️ 1D多头但4H已转空，做多风险极高，只允许做空")
            else:
                return ("weak_bull",
                        ["track_4", "track_2"],
                        ["track_1", "track_3"],
                        "1D多头但4H震荡，只允许回踩做多或见顶做空")

        # 震荡
        return ("ranging",
                ["track_2", "track_3"],
                ["track_1", "track_4"],
                "无明确趋势，短线双向操作，条件加严")

    # ================= 分档入场计划 =================
    def _build_entry_plan(self, direction, cp, atr, rh, rl, ma10, boll_mid, trigger_type):
        stages = []
        note = ""

        if trigger_type == "long_trend":
            if atr:
                stages.append({"weight": 40, "type": "limit", "price": cp - 0.5 * atr,
                               "note": f"限价挂在 {cp - 0.5*atr:.4f}，等回踩"})
                stages.append({"weight": 35, "type": "limit", "price": cp - 1.0 * atr,
                               "note": f"限价挂在 {cp - 1.0*atr:.4f}"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.995, "note": "限价-0.5%"})
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.99, "note": "限价-1%"})
            if rl:
                stages.append({"weight": 25, "type": "limit", "price": rl * 1.005, "note": f"近期低点上方"})
            else:
                stages.append({"weight": 25, "type": "limit", "price": cp * 0.98, "note": "限价-2%"})
            stop = (rl - 2.0 * atr) if (rl and atr) else None
            note = "底部突破后往往有回踩，限价单接飞刀"

        elif trigger_type == "short_reversal":
            stages.append({"weight": 70, "type": "market", "price": cp, "note": "顶部窗口极短，立即市价"})
            if rh:
                stages.append({"weight": 30, "type": "limit", "price": rh * 1.01, "note": f"反弹到前高上方加仓"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 1.02, "note": "反弹+2%"})
            stop = (rh + 2.0 * atr) if (rh and atr) else None
            note = "见顶反转窗口极短，首档必须市价"

        elif trigger_type == "short_trend_follow":
            if boll_mid and boll_mid >= cp:
                stages.append({"weight": 60, "type": "limit", "price": boll_mid, "note": "限价挂在1H布林中轨"})
                stages.append({"weight": 40, "type": "limit", "price": boll_mid * 1.015, "note": "布林中轨上方1.5%"})
            elif ma10 and ma10 >= cp:
                stages.append({"weight": 60, "type": "limit", "price": ma10, "note": "限价挂在30m MA10"})
                stages.append({"weight": 40, "type": "limit", "price": ma10 * 1.02, "note": "MA10上方2%"})
            else:
                stages.append({"weight": 60, "type": "limit", "price": cp * 1.01, "note": "限价+1%"})
                stages.append({"weight": 40, "type": "limit", "price": cp * 1.02, "note": "限价+2%"})
            if boll_mid and atr:
                stop = boll_mid * 1.02 + 1.0 * atr
            elif ma10 and atr:
                stop = ma10 * 1.02 + 1.0 * atr
            else:
                stop = cp * 1.05
            note = "下跌中继的反弹很磨人，等反弹到阻力位挂限价空单"

        elif trigger_type == "long_rebound":
            # 🚀 抄底用限价，绝不追
            if atr:
                stages.append({"weight": 35, "type": "limit", "price": cp - 0.5 * atr, "note": "限价-0.5ATR"})
                stages.append({"weight": 35, "type": "limit", "price": cp - 1.5 * atr, "note": "限价-1.5ATR"})
            else:
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.99, "note": "限价-1%"})
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.97, "note": "限价-3%"})
            if rl:
                stages.append({"weight": 30, "type": "limit", "price": rl * 0.99, "note": "近期低点下方"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 0.95, "note": "限价-5%"})
            stop = (rl - 2.0 * atr) if (rl and atr) else None
            note = "暴跌后往往有二次探底，全部限价，避免抄在半山腰"

        elif trigger_type == "long_pullback":
            stages.append({"weight": 60, "type": "market", "price": cp, "note": "已到回踩位，市价占位"})
            if boll_mid:
                stages.append({"weight": 40, "type": "limit", "price": boll_mid * 0.99, "note": "布林中轨下方1%"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.98, "note": "限价-2%"})
            stop = (ma10 - 1.5 * atr) if (ma10 and atr) else (rl - 2.0 * atr if (rl and atr) else None)
            note = "趋势中回踩到位即入场"

        total_weight = sum(s["weight"] for s in stages)
        avg_price = sum(s["price"] * s["weight"] for s in stages) / total_weight if total_weight > 0 else cp
        return {"stages": stages, "avg_price": avg_price, "stop": stop, "note": note}

    # ================= 主评估 =================
    def evaluate(self, symbol, asset_type, market_data):
        is_spot_mode = market_data.get("data_mode") == "spot"

        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        adx = market_data.get("adx")
        klines = market_data.get("klines_30m", [])
        fp = market_data.get("funding_percentile")
        trend_4h = market_data.get("trend_4h")
        trend_1d = market_data.get("trend_1d")
        rsi_1h = market_data.get("rsi_1h")
        rsi_4h = market_data.get("rsi_4h")
        kdj_1h = market_data.get("kdj_1h") or {}
        boll_1h = market_data.get("boll_1h") or {}
        macd_1h = market_data.get("macd_1h") or {}
        macd_4h = market_data.get("macd_4h") or {}
        rsi_div = market_data.get("rsi_div_1h")
        atr = market_data.get("atr")
        boll_mid = boll_1h.get("mid")
        ema20_4h = market_data.get("ema20_4h")
        ema50_4h = market_data.get("ema50_4h")

        # 🚀 第一步：确定市场状态
        regime, allowed, forbidden, regime_desc = self._determine_regime(market_data)
        momentum_4h = self._calc_4h_momentum(market_data)

        result = {
            "regime": regime,
            "regime_desc": regime_desc,
            "momentum_4h": momentum_4h,
            "allowed_tracks": allowed,
            "forbidden_tracks": forbidden,
            "track_1": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "allowed": "track_1" in allowed},
            "track_2": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "allowed": "track_2" in allowed, "sub_type": None},
            "track_3": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "allowed": "track_3" in allowed},
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
        rsi_4h_str = f"{rsi_4h:.1f}" if rsi_4h is not None else "N/A"
        kdj_j_str = f"{kdj_1h['j']:.1f}" if kdj_1h.get("j") is not None else "N/A"

        adx_ok_trend = adx is not None and adx >= 20
        adx_ok_reversal = adx is not None and adx >= 12

        cvd_series = []
        if klines:
            cvd = 0
            for k in klines:
                cvd += k["volume"] * (1 if k["close"] >= k["open"] else -1)
                cvd_series.append(cvd)

        # ================= 轨道1：底部突破做多 =================
        if not result["track_1"]["allowed"]:
            result["track_1"]["details"]["❌ 状态禁止"] = f"当前状态【{regime}】禁止此轨道"
        elif trend_1d == "down" or trend_4h == "down":
            result["track_1"]["details"]["硬条件"] = f"❌ 趋势逆风（1D={trend_1d or '?'}, 4H={trend_4h or '?'}）"
        else:
            result["track_1"]["hard_ok"] = True
            result["track_1"]["details"]["硬条件"] = f"✅ 1D={trend_1d} + 4H={trend_4h}"

            if price <= rl * 1.05:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.底部区域"] = f"✅ 现价{price}，低点{rl}"
            else:
                result["track_1"]["details"]["1.底部区域"] = f"❌ 高于低点{rl}的1.05倍"

            if ma10 and price > ma10:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["2.站上30m MA10"] = f"✅ {price} > {ma10:.4f}"
            else:
                ma10_str = f"{ma10:.4f}" if ma10 else "N/A"
                result["track_1"]["details"]["2.站上30m MA10"] = f"❌ {price} < {ma10_str}"

            if 45 <= rsi <= 65 or (rsi_1h is not None and rsi_1h < 40):
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["3.30m RSI温和 或 1H超卖"] = f"✅ 30m RSI={rsi_str}, 1h RSI={rsi_1h_str}"
            else:
                result["track_1"]["details"]["3.30m RSI温和 或 1H超卖"] = f"❌ 30m RSI={rsi_str}"

            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["volume"] > avg_vol * 1.3 and klines[-1]["close"] > klines[-1]["open"]:
                    result["track_1"]["score"] += 1
                    result["track_1"]["details"]["4.30m放量阳线"] = "✅ 底部放量"
                else:
                    result["track_1"]["details"]["4.30m放量阳线"] = "❌ 未见放量阳线"
            else:
                result["track_1"]["details"]["4.30m放量阳线"] = "❌ 数据不足"

            if kdj_1h.get("j") is not None and kdj_1h["j"] < 20:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"✅ J={kdj_j_str}"
            else:
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_j_str}"

            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] > macd_4h["dea"]:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["6.4H MACD金叉"] = "✅ DIF > DEA"
            else:
                result["track_1"]["details"]["6.4H MACD金叉"] = "❌ 4H MACD未金叉"

        # ================= 轨道2：做空 =================
        if not result["track_2"]["allowed"]:
            result["track_2"]["details"]["❌ 状态禁止"] = f"当前状态【{regime}】禁止做空"
        else:
            # 做空硬条件
            if momentum_4h in ("bull", "strong_bull"):
                result["track_2"]["details"]["硬条件"] = "❌ 4H动能向上，禁止做空"
            elif trend_1d == "up" and momentum_4h == "neutral":
                result["track_2"]["details"]["硬条件"] = "❌ 日线多头 + 4H震荡，做空需4H明确转空"
            elif not adx_ok_trend:
                adx_str = f"{adx:.1f}" if adx is not None else "N/A"
                result["track_2"]["details"]["硬条件"] = f"❌ ADX={adx_str} < 20"
            else:
                result["track_2"]["hard_ok"] = True
                result["track_2"]["details"]["硬条件"] = f"✅ 1D={trend_1d} + 4H={momentum_4h} + ADX≥20"

                # 2A 见顶做空
                result["track_2"]["details"]["──────── 📌 见顶做空（反转）────────"] = ""
                rev_score = 0

                if price >= rh * 0.97:
                    rev_score += 1
                    result["track_2"]["details"]["A1.逼近高点"] = f"✅ {price} 接近 {rh}"
                else:
                    result["track_2"]["details"]["A1.逼近高点"] = f"❌ 距高{((rh-price)/rh*100):.1f}%"

                if rsi >= 70:
                    rev_score += 1
                    result["track_2"]["details"]["A2.30m RSI超买"] = f"✅ RSI={rsi_str}"
                else:
                    result["track_2"]["details"]["A2.30m RSI超买"] = f"❌ RSI={rsi_str}"

                if cvd_series and len(cvd_series) >= 10:
                    high_now = max(k["high"] for k in klines[-3:])
                    high_prev = max(k["high"] for k in klines[-10:-3])
                    cvd_now = cvd_series[-1]
                    cvd_prev = max(cvd_series[-10:-3])
                    if high_now > high_prev and cvd_now < cvd_prev * 0.95:
                        rev_score += 1
                        result["track_2"]["details"]["A3.30m CVD顶背离"] = "✅ 价新高CVD未新高"
                    else:
                        result["track_2"]["details"]["A3.30m CVD顶背离"] = "❌ 未出现"
                else:
                    result["track_2"]["details"]["A3.30m CVD顶背离"] = "❌ 数据不足"

                if (rsi_1h is not None and rsi_1h >= 70) or rsi_div == "bearish":
                    rev_score += 1
                    result["track_2"]["details"]["A4.1H RSI超买或顶背离"] = f"✅ 1h RSI={rsi_1h_str}"
                else:
                    result["track_2"]["details"]["A4.1H RSI超买或顶背离"] = f"❌ 1h RSI={rsi_1h_str}"

                if kdj_1h.get("j") is not None and kdj_1h["j"] > 100:
                    rev_score += 1
                    result["track_2"]["details"]["A5.1H KDJ超买"] = f"✅ J={kdj_j_str}"
                else:
                    result["track_2"]["details"]["A5.1H KDJ超买"] = f"❌ J={kdj_j_str}"

                if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] < macd_4h["dea"]:
                    rev_score += 1
                    result["track_2"]["details"]["A6.4H MACD死叉"] = "✅ DIF < DEA"
                else:
                    result["track_2"]["details"]["A6.4H MACD死叉"] = "❌ 4H MACD未死叉"

                result["track_2"]["details"]["📊 见顶做空得分"] = f"{rev_score}/6"

                # 2B 顺势做空
                result["track_2"]["details"]["──────── 📉 顺势做空（趋势延续）────────"] = ""
                tf_core = 0
                tf_aux = 0

                if ema20_4h and ema50_4h and ema20_4h < ema50_4h:
                    tf_core += 1
                    result["track_2"]["details"]["B1.4H空头结构"] = f"✅ EMA20<EMA50"
                else:
                    result["track_2"]["details"]["B1.4H空头结构"] = f"❌ EMA20未低于EMA50"

                if boll_mid:
                    dist = (price - boll_mid) / boll_mid
                    if dist >= -0.005:
                        tf_core += 1
                        result["track_2"]["details"]["B2.价格接近/高于布林中轨"] = f"✅ 距离{dist*100:+.1f}%"
                    else:
                        result["track_2"]["details"]["B2.价格接近/高于布林中轨"] = f"❌ 已跌破中轨{dist*100:.1f}%"
                else:
                    result["track_2"]["details"]["B2.价格接近/高于布林中轨"] = "❌ 数据缺失"

                if rsi_1h is not None and 40 <= rsi_1h <= 60:
                    tf_aux += 1
                    result["track_2"]["details"]["B3.1H RSI中位"] = f"✅ RSI={rsi_1h_str}"
                else:
                    result["track_2"]["details"]["B3.1H RSI中位"] = f"❌ RSI={rsi_1h_str}"

                if klines and len(klines) >= 2:
                    last, prev = klines[-1], klines[-2]
                    body = abs(last["close"] - last["open"])
                    upper_shadow = last["high"] - max(last["close"], last["open"])
                    bearish_engulf = (prev["close"] > prev["open"] and last["close"] < last["open"]
                                      and last["open"] >= prev["close"] and last["close"] <= prev["open"])
                    if body > 0 and upper_shadow > body * 1.2:
                        tf_aux += 1
                        result["track_2"]["details"]["B4.30m反弹遇阻"] = "✅ 长上影线"
                    elif bearish_engulf:
                        tf_aux += 1
                        result["track_2"]["details"]["B4.30m反弹遇阻"] = "✅ 看跌吞没"
                    else:
                        result["track_2"]["details"]["B4.30m反弹遇阻"] = "❌ 未见遇阻形态"
                else:
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "❌ 数据不足"

                if macd_1h.get("dif") is not None and macd_1h.get("dea") is not None and macd_1h["dif"] < macd_1h["dea"]:
                    tf_aux += 1
                    result["track_2"]["details"]["B5.1H MACD空头"] = "✅ DIF < DEA"
                else:
                    result["track_2"]["details"]["B5.1H MACD空头"] = "❌ 1H MACD未死叉"

                if rsi < 60:
                    tf_aux += 1
                    result["track_2"]["details"]["B6.30m RSI未超买"] = f"✅ RSI={rsi_str}"
                else:
                    result["track_2"]["details"]["B6.30m RSI未超买"] = f"❌ RSI={rsi_str}"

                result["track_2"]["details"]["📊 顺势做空核心"] = f"{tf_core}/2"
                result["track_2"]["details"]["📊 顺势做空辅助"] = f"{tf_aux}/4"

                if rev_score >= 4:
                    result["track_2"]["sub_type"] = "reversal"
                    result["track_2"]["score"] = rev_score
                    result["track_2"]["reason"] = f"见顶做空 {rev_score}/6"
                elif tf_core >= 2 and tf_aux >= 2:
                    result["track_2"]["sub_type"] = "trend_follow"
                    result["track_2"]["score"] = tf_core + tf_aux
                    result["track_2"]["reason"] = f"顺势做空 核心{tf_core}/2 + 辅助{tf_aux}/4"

        # ================= 轨道3：暴跌反弹做多 =================
        if not result["track_3"]["allowed"]:
            result["track_3"]["details"]["❌ 状态禁止"] = f"当前状态【{regime}】禁止此轨道"
        elif trend_1d == "down":
            result["track_3"]["details"]["硬条件"] = "❌ 1D趋势向下，禁止抄底"
        elif not adx_ok_reversal:
            adx_str = f"{adx:.1f}" if adx is not None else "N/A"
            result["track_3"]["details"]["硬条件"] = f"❌ ADX={adx_str} < 12"
        # 🚀 核心加固：4H动能是bear/strong_bear时，必须满足极端超卖条件
        elif momentum_4h in ("bear", "strong_bear"):
            # 严格条件：RSI_4H < 20 + RSI_30m < 20 + 1H KDJ J < -10 + CVD底背离
            extreme_oversold = (
                (rsi_4h is not None and rsi_4h < 20) and
                (rsi < 25) and
                (kdj_1h.get("j") is not None and kdj_1h["j"] < -10) and
                rsi_div == "bullish"
            )
            if not extreme_oversold:
                result["track_3"]["details"]["硬条件"] = f"❌ 4H动能={momentum_4h}，只有极端超卖才允许抄底"
                result["track_3"]["details"]["🔒 4H下跌中继保护"] = "需 RSI_4H<20 + RSI_30m<25 + KDJ_1H J<-10 + RSI底背离 全部满足"
            else:
                result["track_3"]["hard_ok"] = True
                result["track_3"]["details"]["硬条件"] = f"✅ 4H下跌但已极端超卖，短线反弹机会"
                result["track_3"]["score"] = 6  # 强制满分触发
        else:
            result["track_3"]["hard_ok"] = True
            result["track_3"]["details"]["硬条件"] = f"✅ 1D={trend_1d} + ADX≥12"

        if result["track_3"]["hard_ok"]:
            if result["track_3"]["score"] < 6:
                if price <= rh * 0.85:
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["1.大幅回撤"] = f"✅ 回撤{((rh-price)/rh*100):.1f}%"
                else:
                    result["track_3"]["details"]["1.大幅回撤"] = f"❌ 回撤{((rh-price)/rh*100):.1f}% < 15%"

                if rsi <= 35:
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["2.30m RSI超卖"] = f"✅ RSI={rsi_str}"
                else:
                    result["track_3"]["details"]["2.30m RSI超卖"] = f"❌ RSI={rsi_str}"

                if cvd_series and len(cvd_series) >= 10:
                    low_now = min(k["low"] for k in klines[-3:])
                    low_prev = min(k["low"] for k in klines[-10:-3])
                    cvd_now = cvd_series[-1]
                    cvd_prev = min(cvd_series[-10:-3])
                    if low_now < low_prev and cvd_now > cvd_prev * 1.05:
                        result["track_3"]["score"] += 1
                        result["track_3"]["details"]["3.30m CVD牛背离"] = "✅ 价新低CVD回升"
                    else:
                        result["track_3"]["details"]["3.30m CVD牛背离"] = "❌ 未见底背离"
                else:
                    result["track_3"]["details"]["3.30m CVD牛背离"] = "❌ 数据不足"

                if (rsi_1h is not None and rsi_1h < 30) or rsi_div == "bullish":
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["4.1H RSI超卖或底背离"] = f"✅ 1h RSI={rsi_1h_str}"
                else:
                    result["track_3"]["details"]["4.1H RSI超卖或底背离"] = f"❌ 1h RSI={rsi_1h_str}"

                if kdj_1h.get("j") is not None and kdj_1h["j"] < 0:
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["5.1H KDJ超卖"] = f"✅ J={kdj_j_str}"
                else:
                    result["track_3"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_j_str}"

                if klines and len(klines) >= 2:
                    last = klines[-1]
                    body = abs(last["close"] - last["open"])
                    lower_shadow = min(last["close"], last["open"]) - last["low"]
                    if body > 0 and lower_shadow > body * 1.5:
                        result["track_3"]["score"] += 1
                        result["track_3"]["details"]["6.30m止跌形态"] = "✅ 锤头线"
                    else:
                        result["track_3"]["details"]["6.30m止跌形态"] = "❌ 未见止跌形态"
                else:
                    result["track_3"]["details"]["6.30m止跌形态"] = "❌ 数据不足"

        # ================= 轨道4：趋势回踩做多 =================
        if not result["track_4"]["allowed"]:
            result["track_4"]["details"]["❌ 状态禁止"] = f"当前状态【{regime}】禁止此轨道"
        elif trend_1d != "up" or trend_4h != "up":
            result["track_4"]["details"]["硬条件"] = f"❌ 需1D多头+4H多头"
        else:
            result["track_4"]["hard_ok"] = True
            result["track_4"]["details"]["硬条件"] = "✅ 1D多头 + 4H多头"

            if boll_mid and abs(price - boll_mid) / boll_mid <= 0.02:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["1.回踩1H布林中轨"] = f"✅ 现价{price:.4f}，中轨{boll_mid:.4f}"
            else:
                boll_mid_str = f"{boll_mid:.4f}" if boll_mid else "N/A"
                result["track_4"]["details"]["1.回踩1H布林中轨"] = f"❌ 距中轨{boll_mid_str}较远"

            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["volume"] < avg_vol * 0.8:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["2.缩量回踩"] = "✅ 缩量"
                else:
                    result["track_4"]["details"]["2.缩量回踩"] = "❌ 未缩量"
            else:
                result["track_4"]["details"]["2.缩量回踩"] = "❌ 数据不足"

            if klines and len(klines) >= 2:
                last = klines[-1]
                body = abs(last["close"] - last["open"])
                lower_shadow = min(last["close"], last["open"]) - last["low"]
                if body > 0 and lower_shadow > body * 1.2:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["3.30m止跌形态"] = "✅ 长下影"
                else:
                    result["track_4"]["details"]["3.30m止跌形态"] = "❌ 未见止跌"
            else:
                result["track_4"]["details"]["3.30m止跌形态"] = "❌ 数据不足"

            if rsi_1h is not None and 40 <= rsi_1h <= 55:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["4.1H RSI健康"] = f"✅ RSI={rsi_1h_str}"
            else:
                result["track_4"]["details"]["4.1H RSI健康"] = f"❌ RSI={rsi_1h_str}"

            if ma10 and price > ma10:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["5.站上30m MA10"] = f"✅ {price} > {ma10:.4f}"
            else:
                ma10_str = f"{ma10:.4f}" if ma10 else "N/A"
                result["track_4"]["details"]["5.站上30m MA10"] = f"❌ 未站上MA10({ma10_str})"

            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] >= macd_4h["dea"]:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["6.4H MACD健康"] = "✅ DIF >= DEA"
            else:
                result["track_4"]["details"]["6.4H MACD健康"] = "❌ 4H MACD已死叉"

        # ================= 🚀 最终裁决（带冲突检测） =================
        candidates = []

        if result["track_2"]["hard_ok"] and result["track_2"].get("sub_type"):
            candidates.append(("short", "track_2", result["track_2"]["score"], result["track_2"]["reason"]))

        if result["track_4"]["hard_ok"] and result["track_4"]["score"] >= 4:
            candidates.append(("long_pullback", "track_4", result["track_4"]["score"], f"趋势回踩 {result['track_4']['score']}/6"))

        if result["track_3"]["hard_ok"] and result["track_3"]["score"] >= 4:
            candidates.append(("long_rebound", "track_3", result["track_3"]["score"], f"暴跌反弹 {result['track_3']['score']}/6"))

        if result["track_1"]["hard_ok"] and result["track_1"]["score"] >= 4:
            candidates.append(("long_trend", "track_1", result["track_1"]["score"], f"底部突破 {result['track_1']['score']}/6"))

        if candidates:
            # 🚀 优先级：做空 > 趋势回踩 > 暴跌反弹 > 底部突破
            priority_order = {"short": 4, "long_pullback": 3, "long_rebound": 2, "long_trend": 1}
            candidates.sort(key=lambda x: (priority_order.get(x[0], 0), x[2]), reverse=True)

            winner_dir, winner_track, winner_score, winner_reason = candidates[0]
            result["triggered"] = True
            result["direction"] = winner_dir
            result["reason"] = winner_reason

            # 记录冲突
            if len(candidates) > 1:
                losers = [f"{c[1]}({c[3]})" for c in candidates[1:]]
                result["conflict_note"] = f"⚠️ 多轨道冲突！优先选择 [{winner_track}]，被否决：{', '.join(losers)}"

            # 生成入场计划
            trigger_type_map = {"short": "short_trend_follow" if result["track_2"]["sub_type"] == "trend_follow" else "short_reversal",
                                "long_pullback": "long_pullback",
                                "long_rebound": "long_rebound",
                                "long_trend": "long_trend"}
            tt = trigger_type_map.get(winner_dir, "long_trend")
            result["entry_plan"] = self._build_entry_plan(winner_dir, price, atr, rh, rl, ma10, boll_mid, tt)

            if is_spot_mode and winner_dir == "short":
                result["direction"] = "spot_warning"
                result["reason"] = "现货逃顶预警（" + winner_reason + "）"
                result["entry_plan"] = None

        return result
