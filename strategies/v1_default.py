from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长多周期共振策略"

    def _build_entry_plan(self, direction, cp, atr, rh, rl, ma10, boll_mid, trigger_type):
        """
        根据轨道类型生成分档入场计划。
        返回：{"stages": [{weight, type, price, note}], "avg_price": xxx, "stop": xxx, "note": xxx}
        """
        stages = []
        note = ""

        if trigger_type == "long_trend":
            # 底部突破：60%市价 + 40%回踩MA10
            stage1_price = cp
            stages.append({"weight": 60, "type": "market", "price": stage1_price, "note": "立即市价，抓住突破"})
            if ma10 and ma10 < cp:
                stages.append({"weight": 40, "type": "limit", "price": ma10 * 0.998, "note": f"回踩30m MA10({ma10:.4f})加仓"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.98, "note": "回踩-2%加仓"})
            stop = (rl - 2.0 * atr) if (rl and atr) else None
            note = "底部突破初期，分两档入场，即使回踩也有子弹"

        elif trigger_type == "short_reversal":
            # 见顶做空：70%市价 + 30%反弹到前高+1%
            stages.append({"weight": 70, "type": "market", "price": cp, "note": "立即市价，顶部窗口极短"})
            if rh:
                stages.append({"weight": 30, "type": "limit", "price": rh * 1.01, "note": f"反弹到前高上方({rh*1.01:.4f})再空"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 1.02, "note": "反弹+2%再空"})
            stop = (rh + 2.0 * atr) if (rh and atr) else None
            note = "顶部反转需要快速占位，70%市价不要犹豫"

        elif trigger_type == "short_trend_follow":
            # 顺势做空：40%市价 + 60%反弹到1H布林中轨
            # 如果当前价已经接近1H布林中轨，直接市价满仓
            if boll_mid and abs(cp - boll_mid) / boll_mid <= 0.005:
                stages.append({"weight": 100, "type": "market", "price": cp, "note": "已到1H布林中轨附近，直接满仓"})
            else:
                stages.append({"weight": 40, "type": "market", "price": cp, "note": "先建底仓"})
                if boll_mid and boll_mid > cp:
                    stages.append({"weight": 60, "type": "limit", "price": boll_mid * 0.998, "note": f"等反弹到1H布林中轨({boll_mid:.4f})再空"})
                elif ma10 and ma10 > cp:
                    stages.append({"weight": 60, "type": "limit", "price": ma10 * 0.998, "note": f"等反弹到30m MA10({ma10:.4f})再空"})
                else:
                    stages.append({"weight": 60, "type": "limit", "price": cp * 1.02, "note": "反弹+2%再空"})
            stop = None
            # 止损放在布林中轨上方或MA10上方
            if boll_mid and atr:
                stop = boll_mid + 1.5 * atr
            elif ma10 and atr:
                stop = ma10 + 1.5 * atr
            elif rh and atr:
                stop = rh + 1.5 * atr
            note = "下跌中继的反弹很磨人，等反弹到关键位再空胜率更高"

        elif trigger_type == "long_rebound":
            # 暴跌反弹：30%市价 + 40%探底位 + 30%极值位
            stages.append({"weight": 30, "type": "market", "price": cp, "note": "立即市价，抓第一波反弹"})
            if atr and rl:
                stages.append({"weight": 40, "type": "limit", "price": cp - 1.0 * atr, "note": f"下跌1倍ATR({cp - atr:.4f})补仓"})
                stages.append({"weight": 30, "type": "limit", "price": rl * 0.99, "note": f"二次探底到前低附近({rl*0.99:.4f})加仓"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.97, "note": "下跌-3%补仓"})
                stages.append({"weight": 30, "type": "limit", "price": cp * 0.95, "note": "下跌-5%加仓"})
            stop = (rl - 2.0 * atr) if (rl and atr) else None
            note = "暴跌后往往有二次探底，分三档挂单防止一次被打光"

        elif trigger_type == "long_pullback":
            # 趋势回踩：70%市价 + 30%回踩布林中轨下方
            stages.append({"weight": 70, "type": "market", "price": cp, "note": "回踩到位，立即市价"})
            if boll_mid:
                stages.append({"weight": 30, "type": "limit", "price": boll_mid * 0.99, "note": f"更深回踩到布林中轨下方({boll_mid*0.99:.4f})加仓"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 0.98, "note": "回踩-2%加仓"})
            stop = (ma10 - 1.5 * atr) if (ma10 and atr) else (rl - 2.0 * atr if (rl and atr) else None)
            note = "趋势中的回踩是加仓机会，仓位可以重一些"

        # 计算加权平均入场价
        total_weight = sum(s["weight"] for s in stages)
        avg_price = sum(s["price"] * s["weight"] for s in stages) / total_weight if total_weight > 0 else cp

        return {
            "stages": stages,
            "avg_price": avg_price,
            "stop": stop,
            "note": note,
        }

    def evaluate(self, symbol, asset_type, market_data):
        is_spot_mode = market_data.get("data_mode") == "spot"

        result = {
            "track_1": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "底部突破做多"},
            "track_2": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "做空", "sub_type": None},
            "track_3": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "暴跌反弹做多"},
            "track_4": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "趋势回踩做多"},
            "triggered": False, "direction": None, "reason": "",
            "entry_plan": None,
            "data_mode": "spot" if is_spot_mode else "futures",
            "multi_tf": {
                "trend_1d": market_data.get("trend_1d"),
                "trend_4h": market_data.get("trend_4h"),
                "rsi_1h": market_data.get("rsi_1h"),
                "rsi_4h": market_data.get("rsi_4h"),
                "rsi_1d": market_data.get("rsi_1d"),
            }
        }

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
        kdj_1h = market_data.get("kdj_1h") or {}
        boll_1h = market_data.get("boll_1h") or {}
        macd_1h = market_data.get("macd_1h") or {}
        macd_4h = market_data.get("macd_4h") or {}
        rsi_div = market_data.get("rsi_div_1h")
        atr = market_data.get("atr")
        boll_mid = boll_1h.get("mid")

        if price is None or rsi is None or rh is None or rl is None:
            result["track_1"]["details"]["数据不足"] = "❌ 核心指标缺失"
            return result

        rsi_str = f"{rsi:.1f}"
        rsi_1h_str = f"{rsi_1h:.1f}" if rsi_1h is not None else "N/A"
        kdj_j_str = f"{kdj_1h['j']:.1f}" if kdj_1h.get("j") is not None else "N/A"

        adx_ok_trend = adx is not None and adx >= 20
        adx_ok_reversal = adx is not None and adx >= 12

        cvd_series = []
        if klines:
            cvd = 0
            for k in klines:
                cvd += k["volume"] * (1 if k["close"] >= k["open"] else -1)
                cvd_series.append(cvd)

        # ============ 轨道1：底部突破做多 ============
        if trend_1d == "down" or trend_4h == "down":
            result["track_1"]["details"]["硬条件"] = f"❌ 大趋势逆风（1D={trend_1d or '?'}, 4H={trend_4h or '?'}）"
        else:
            result["track_1"]["hard_ok"] = True
            result["track_1"]["details"]["硬条件"] = f"✅ 1D={trend_1d or '?'} + 4H={trend_4h or '?'} 允许做多"

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
                    result["track_1"]["details"]["4.30m放量阳线"] = "✅ 底部放量买入"
                else:
                    result["track_1"]["details"]["4.30m放量阳线"] = "❌ 未见放量阳线"
            else:
                result["track_1"]["details"]["4.30m放量阳线"] = "❌ 数据不足"

            if kdj_1h.get("j") is not None and kdj_1h["j"] < 20:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"✅ KDJ J={kdj_j_str} < 20"
            else:
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_j_str}"

            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] > macd_4h["dea"]:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["6.4H MACD金叉"] = "✅ DIF > DEA"
            else:
                result["track_1"]["details"]["6.4H MACD金叉"] = "❌ 4H MACD未金叉"

        # ============ 轨道2：做空 ============
        if trend_1d == "up" or trend_4h == "up":
            result["track_2"]["details"]["硬条件"] = f"❌ 大趋势逆风（1D={trend_1d or '?'}, 4H={trend_4h or '?'}）"
        elif not adx_ok_trend:
            adx_str = f"{adx:.1f}" if adx is not None else "N/A"
            result["track_2"]["details"]["硬条件"] = f"❌ ADX={adx_str} < 20，市场无序震荡"
        else:
            result["track_2"]["hard_ok"] = True
            result["track_2"]["details"]["硬条件"] = f"✅ 1D={trend_1d or '?'} + 4H={trend_4h or '?'} + ADX≥20"

            # 2A 见顶做空
            result["track_2"]["details"]["──────── 📌 见顶做空（反转）────────"] = ""
            reversal_score = 0

            if price >= rh * 0.97:
                reversal_score += 1
                result["track_2"]["details"]["A1.逼近高点"] = f"✅ {price} 接近 {rh}"
            else:
                result["track_2"]["details"]["A1.逼近高点"] = f"❌ 距高{((rh-price)/rh*100):.1f}%"

            if rsi >= 70:
                reversal_score += 1
                result["track_2"]["details"]["A2.30m RSI超买"] = f"✅ RSI={rsi_str}"
            else:
                result["track_2"]["details"]["A2.30m RSI超买"] = f"❌ RSI={rsi_str}"

            if cvd_series and len(cvd_series) >= 10:
                high_now = max(k["high"] for k in klines[-3:])
                high_prev = max(k["high"] for k in klines[-10:-3])
                cvd_now = cvd_series[-1]
                cvd_prev = max(cvd_series[-10:-3])
                if high_now > high_prev and cvd_now < cvd_prev * 0.95:
                    reversal_score += 1
                    result["track_2"]["details"]["A3.30m CVD顶背离"] = "✅ 价新高CVD未新高"
                else:
                    result["track_2"]["details"]["A3.30m CVD顶背离"] = "❌ 未出现"
            else:
                result["track_2"]["details"]["A3.30m CVD顶背离"] = "❌ 数据不足"

            if (rsi_1h is not None and rsi_1h >= 70) or rsi_div == "bearish":
                reversal_score += 1
                rsi_div_str = rsi_div if rsi_div else "无"
                result["track_2"]["details"]["A4.1H RSI超买或顶背离"] = f"✅ 1h RSI={rsi_1h_str}, 背离={rsi_div_str}"
            else:
                result["track_2"]["details"]["A4.1H RSI超买或顶背离"] = f"❌ 1h RSI={rsi_1h_str}"

            if kdj_1h.get("j") is not None and kdj_1h["j"] > 100:
                reversal_score += 1
                result["track_2"]["details"]["A5.1H KDJ超买"] = f"✅ J={kdj_j_str} > 100"
            else:
                result["track_2"]["details"]["A5.1H KDJ超买"] = f"❌ J={kdj_j_str}"

            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] < macd_4h["dea"]:
                reversal_score += 1
                result["track_2"]["details"]["A6.4H MACD死叉"] = "✅ DIF < DEA"
            else:
                result["track_2"]["details"]["A6.4H MACD死叉"] = "❌ 4H MACD未死叉"

            result["track_2"]["details"]["📊 见顶做空得分"] = f"{reversal_score}/6"

            # 2B 顺势做空
            result["track_2"]["details"]["──────── 📉 顺势做空（趋势延续）────────"] = ""
            tf_score = 0

            if trend_4h == "down":
                tf_score += 1
                result["track_2"]["details"]["B1.4H空头排列"] = "✅ EMA20 < EMA50 且价格 < EMA20"
            else:
                result["track_2"]["details"]["B1.4H空头排列"] = f"❌ 4H趋势={trend_4h or '?'}"

            if ma10 and price < ma10:
                tf_score += 1
                result["track_2"]["details"]["B2.跌破30m MA10"] = f"✅ {price} < {ma10:.4f}"
            else:
                ma10_str = f"{ma10:.4f}" if ma10 else "N/A"
                result["track_2"]["details"]["B2.跌破30m MA10"] = f"❌ {price} 仍在MA10({ma10_str})上方"

            drawdown = (rh - price) / rh if rh else 0
            if 0.05 <= drawdown <= 0.15:
                tf_score += 1
                result["track_2"]["details"]["B3.回撤区间"] = f"✅ 距高点回撤{drawdown*100:.1f}%"
            else:
                result["track_2"]["details"]["B3.回撤区间"] = f"❌ 回撤{drawdown*100:.1f}%（需5%-15%）"

            if klines and len(klines) >= 2:
                last, prev = klines[-1], klines[-2]
                body = abs(last["close"] - last["open"])
                upper_shadow = last["high"] - max(last["close"], last["open"])
                bearish_engulf = (prev["close"] > prev["open"] and last["close"] < last["open"]
                                  and last["open"] >= prev["close"] and last["close"] <= prev["open"])
                if body > 0 and upper_shadow > body * 1.5:
                    tf_score += 1
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "✅ 长上影线"
                elif bearish_engulf:
                    tf_score += 1
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "✅ 看跌吞没"
                else:
                    result["track_2"]["details"]["B4.30m反弹遇阻"] = "❌ 未见遇阻形态"
            else:
                result["track_2"]["details"]["B4.30m反弹遇阻"] = "❌ 数据不足"

            if macd_1h.get("hist") is not None and macd_1h["hist"] < 0:
                tf_score += 1
                macd_hist_str = f"{macd_1h['hist']:.4f}"
                result["track_2"]["details"]["B5.1H MACD柱为负"] = f"✅ 柱值={macd_hist_str}"
            else:
                result["track_2"]["details"]["B5.1H MACD柱为负"] = "❌ 1H MACD柱为正"

            if rsi_1h is not None and rsi_1h < 50:
                tf_score += 1
                result["track_2"]["details"]["B6.1H RSI偏弱"] = f"✅ RSI={rsi_1h_str} < 50"
            else:
                result["track_2"]["details"]["B6.1H RSI偏弱"] = f"❌ RSI={rsi_1h_str}"

            result["track_2"]["details"]["📊 顺势做空得分"] = f"{tf_score}/6"

            if reversal_score >= 4:
                result["track_2"]["sub_type"] = "reversal"
                result["track_2"]["score"] = reversal_score
                result["track_2"]["reason"] = f"见顶做空 {reversal_score}/6 触发"
            elif tf_score >= 4:
                result["track_2"]["sub_type"] = "trend_follow"
                result["track_2"]["score"] = tf_score
                result["track_2"]["reason"] = f"顺势做空 {tf_score}/6 触发"

        # ============ 轨道3：暴跌反弹做多 ============
        if trend_1d == "down":
            result["track_3"]["details"]["硬条件"] = "❌ 1D趋势向下，禁止抄底"
        elif not adx_ok_reversal:
            adx_str = f"{adx:.1f}" if adx is not None else "N/A"
            result["track_3"]["details"]["硬条件"] = f"❌ ADX={adx_str} < 12"
        else:
            result["track_3"]["hard_ok"] = True
            result["track_3"]["details"]["硬条件"] = f"✅ 1D={trend_1d or '?'} + ADX≥12"

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
                rsi_div_str = rsi_div if rsi_div else "无"
                result["track_3"]["details"]["4.1H RSI超卖或底背离"] = f"✅ 1h RSI={rsi_1h_str}, 背离={rsi_div_str}"
            else:
                result["track_3"]["details"]["4.1H RSI超卖或底背离"] = f"❌ 1h RSI={rsi_1h_str}"

            if kdj_1h.get("j") is not None and kdj_1h["j"] < 0:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["5.1H KDJ超卖"] = f"✅ J={kdj_j_str} < 0"
            else:
                result["track_3"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_j_str}"

            if klines and len(klines) >= 2:
                last = klines[-1]
                body = abs(last["close"] - last["open"])
                lower_shadow = min(last["close"], last["open"]) - last["low"]
                if body > 0 and lower_shadow > body * 1.5:
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["6.30m止跌形态"] = "✅ 长下影线（锤头线）"
                else:
                    result["track_3"]["details"]["6.30m止跌形态"] = "❌ 未见止跌形态"
            else:
                result["track_3"]["details"]["6.30m止跌形态"] = "❌ 数据不足"

        # ============ 轨道4：趋势回踩做多 ============
        if trend_1d != "up" or trend_4h != "up":
            result["track_4"]["details"]["硬条件"] = f"❌ 需1D多头+4H多头（当前1D={trend_1d or '?'}, 4H={trend_4h or '?'}）"
        else:
            result["track_4"]["hard_ok"] = True
            result["track_4"]["details"]["硬条件"] = "✅ 1D多头 + 4H多头"

            if boll_mid and abs(price - boll_mid) / boll_mid <= 0.02:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["1.回踩1H布林中轨"] = f"✅ 现价{price:.4f}，中轨{boll_mid:.4f}"
            else:
                boll_mid_str = f"{boll_mid:.4f}" if boll_mid else "N/A"
                result["track_4"]["details"]["1.回踩1H布林中轨"] = f"❌ 现价{price} 距中轨{boll_mid_str}较远"

            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["volume"] < avg_vol * 0.8:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["2.缩量回踩"] = "✅ 成交量<前5根均量×0.8"
                else:
                    result["track_4"]["details"]["2.缩量回踩"] = "❌ 回踩未缩量"
            else:
                result["track_4"]["details"]["2.缩量回踩"] = "❌ 数据不足"

            if klines and len(klines) >= 2:
                last = klines[-1]
                body = abs(last["close"] - last["open"])
                lower_shadow = min(last["close"], last["open"]) - last["low"]
                if body > 0 and lower_shadow > body * 1.2:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["3.30m止跌形态"] = "✅ 长下影线"
                else:
                    result["track_4"]["details"]["3.30m止跌形态"] = "❌ 未见止跌形态"
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

        # ============ 最终触发判定 + 生成入场计划 ============
        if result["track_2"]["hard_ok"] and result["track_2"].get("sub_type"):
            result["triggered"] = True
            result["direction"] = "short"
            result["reason"] = result["track_2"]["reason"]
            trigger_type = "short_reversal" if result["track_2"]["sub_type"] == "reversal" else "short_trend_follow"
            result["entry_plan"] = self._build_entry_plan("short", price, atr, rh, rl, ma10, boll_mid, trigger_type)
            if is_spot_mode:
                result["direction"] = "spot_warning"
                result["reason"] = result["track_2"]["reason"].replace("做空", "逃顶预警")
                result["entry_plan"] = None  # 现货不做空，不给入场计划
        elif result["track_4"]["hard_ok"] and result["track_4"]["score"] >= 4:
            result["triggered"] = True
            result["direction"] = "long_pullback"
            result["reason"] = f"趋势回踩做多 {result['track_4']['score']}/6 触发"
            result["entry_plan"] = self._build_entry_plan("long", price, atr, rh, rl, ma10, boll_mid, "long_pullback")
        elif result["track_3"]["hard_ok"] and result["track_3"]["score"] >= 4:
            result["triggered"] = True
            result["direction"] = "long_rebound"
            result["reason"] = f"暴跌反弹 {result['track_3']['score']}/6 触发"
            result["entry_plan"] = self._build_entry_plan("long", price, atr, rh, rl, ma10, boll_mid, "long_rebound")
        elif result["track_1"]["hard_ok"] and result["track_1"]["score"] >= 4:
            result["triggered"] = True
            result["direction"] = "long_trend"
            result["reason"] = f"底部突破 {result['track_1']['score']}/6 触发"
            result["entry_plan"] = self._build_entry_plan("long", price, atr, rh, rl, ma10, boll_mid, "long_trend")

        return result
