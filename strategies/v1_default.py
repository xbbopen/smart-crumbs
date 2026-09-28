from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长多周期共振策略"

    def evaluate(self, symbol, asset_type, market_data):
        is_spot_mode = market_data.get("data_mode") == "spot"

        result = {
            "track_1": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "底部突破做多"},
            "track_2": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "做空", "sub_type": None},
            "track_3": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "暴跌反弹做多"},
            "track_4": {"score": 0, "max": 6, "details": {}, "hard_ok": False, "name": "趋势回踩做多"},
            "triggered": False, "direction": None, "reason": "",
            "data_mode": "spot" if is_spot_mode else "futures",
            # 多周期状态
            "multi_tf": {
                "trend_1d": market_data.get("trend_1d"),
                "trend_4h": market_data.get("trend_4h"),
                "rsi_1h": market_data.get("rsi_1h"),
                "rsi_4h": market_data.get("rsi_4h"),
                "rsi_1d": market_data.get("rsi_1d"),
                "ema50_1d": market_data.get("ema50_1d"),
                "ema20_4h": market_data.get("ema20_4h"),
                "ema50_4h": market_data.get("ema50_4h"),
                "boll_1h": market_data.get("boll_1h"),
                "macd_1h": market_data.get("macd_1h"),
                "macd_4h": market_data.get("macd_4h"),
                "kdj_1h": market_data.get("kdj_1h"),
                "rsi_div_1h": market_data.get("rsi_div_1h"),
            }
        }

        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        adx = market_data.get("adx")
        klines = market_data.get("klines_30m", [])
        klines_1h = market_data.get("klines_1h", [])
        fp = market_data.get("funding_percentile")
        trend_4h = market_data.get("trend_4h")
        trend_1d = market_data.get("trend_1d")
        rsi_1h = market_data.get("rsi_1h")
        rsi_4h = market_data.get("rsi_4h")
        rsi_1d = market_data.get("rsi_1d")
        kdj_1h = market_data.get("kdj_1h") or {}
        boll_1h = market_data.get("boll_1h") or {}
        macd_1h = market_data.get("macd_1h") or {}
        macd_4h = market_data.get("macd_4h") or {}
        rsi_div = market_data.get("rsi_div_1h")

        if price is None or rsi is None or rh is None or rl is None:
            result["track_1"]["details"]["数据不足"] = "❌ 核心指标缺失"
            return result

        # ADX 分层门槛
        adx_ok_trend = adx is not None and adx >= 20
        adx_ok_reversal = adx is not None and adx >= 12

        # 计算CVD
        cvd_series = []
        if klines:
            cvd = 0
            for k in klines:
                cvd += k["volume"] * (1 if k["close"] >= k["open"] else -1)
                cvd_series.append(cvd)

        # ============================================================
        # 轨道1：底部突破做多
        # 硬条件：日线非空头 + 4H非空头
        # ============================================================
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
                result["track_1"]["details"]["2.站上30m MA10"] = f"❌ {price} < {ma10}"

            if 45 <= rsi <= 65 or (rsi_1h and rsi_1h < 40):
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["3.30m RSI温和 或 1H超卖"] = f"✅ 30m RSI={rsi:.1f}, 1h RSI={rsi_1h:.1f if rsi_1h else 'N/A'}"
            else:
                result["track_1"]["details"]["3.30m RSI温和 或 1H超卖"] = f"❌ 30m RSI={rsi:.1f}"

            if klines and len(klines) >= 10:
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
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"✅ KDJ J={kdj_1h['j']:.1f} < 20"
            else:
                result["track_1"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_1h.get('j', 'N/A')}"

            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] > macd_4h["dea"]:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["6.4H MACD金叉"] = "✅ DIF > DEA"
            else:
                result["track_1"]["details"]["6.4H MACD金叉"] = "❌ 4H MACD未金叉"

        # ============================================================
        # 轨道2A：见顶做空（反转）
        # 硬条件：日线非多头 + 4H非多头 + ADX ≥ 20
        # ============================================================
        if trend_1d == "up" or trend_4h == "up":
            result["track_2"]["details"]["硬条件"] = f"❌ 大趋势逆风（1D={trend_1d or '?'}, 4H={trend_4h or '?'}）"
        elif not adx_ok_trend:
            result["track_2"]["details"]["硬条件"] = f"❌ ADX={adx:.1f} < 20，市场无序震荡"
        else:
            result["track_2"]["hard_ok"] = True
            result["track_2"]["details"]["硬条件"] = f"✅ 1D={trend_1d or '?'} + 4H={trend_4h or '?'} + ADX≥20"

            result["track_2"]["details"]["──────── 📌 见顶做空（反转）────────"] = ""
            reversal_score = 0
            # 满分6分：逼近高点/30m RSI超买/30m CVD顶背离/1H RSI超买或顶背离/1H KDJ超买/4H MACD死叉

            if price >= rh * 0.97:
                reversal_score += 1
                result["track_2"]["details"]["A1.逼近高点"] = f"✅ {price} 接近 {rh}"
            else:
                result["track_2"]["details"]["A1.逼近高点"] = f"❌ 距高{((rh-price)/rh*100):.1f}%"

            if rsi >= 70:
                reversal_score += 1
                result["track_2"]["details"]["A2.30m RSI超买"] = f"✅ RSI={rsi:.1f}"
            else:
                result["track_2"]["details"]["A2.30m RSI超买"] = f"❌ RSI={rsi:.1f}"

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

            if (rsi_1h and rsi_1h >= 70) or rsi_div == "bearish":
                reversal_score += 1
                result["track_2"]["details"]["A4.1H RSI超买或顶背离"] = f"✅ 1h RSI={rsi_1h if rsi_1h else 'N/A'}, 背离={rsi_div or '无'}"
            else:
                result["track_2"]["details"]["A4.1H RSI超买或顶背离"] = f"❌ 1h RSI={rsi_1h if rsi_1h else 'N/A'}"

            if kdj_1h.get("j") is not None and kdj_1h["j"] > 100:
                reversal_score += 1
                result["track_2"]["details"]["A5.1H KDJ超买"] = f"✅ J={kdj_1h['j']:.1f} > 100"
            else:
                result["track_2"]["details"]["A5.1H KDJ超买"] = f"❌ J={kdj_1h.get('j', 'N/A')}"

            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] < macd_4h["dea"]:
                reversal_score += 1
                result["track_2"]["details"]["A6.4H MACD死叉"] = "✅ DIF < DEA"
            else:
                result["track_2"]["details"]["A6.4H MACD死叉"] = "❌ 4H MACD未死叉"

            result["track_2"]["details"]["📊 见顶做空得分"] = f"{reversal_score}/6"
            result["track_2"]["_reversal_score"] = reversal_score

            # ============================================================
            # 轨道2B：顺势做空（趋势延续）
            # ============================================================
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
                result["track_2"]["details"]["B2.跌破30m MA10"] = f"❌ {price} 仍在MA10上方"

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
                result["track_2"]["details"]["B5.1H MACD柱为负"] = f"✅ 柱值={macd_1h['hist']:.4f}"
            else:
                result["track_2"]["details"]["B5.1H MACD柱为负"] = "❌ 1H MACD柱为正"

            if rsi_1h is not None and rsi_1h < 50:
                tf_score += 1
                result["track_2"]["details"]["B6.1H RSI偏弱"] = f"✅ RSI={rsi_1h:.1f} < 50"
            else:
                result["track_2"]["details"]["B6.1H RSI偏弱"] = f"❌ RSI={rsi_1h if rsi_1h else 'N/A'}"

            result["track_2"]["details"]["📊 顺势做空得分"] = f"{tf_score}/6"
            result["track_2"]["_tf_score"] = tf_score

            # 最终判定：见顶优先，顺势兜底
            if reversal_score >= 4:
                result["track_2"]["sub_type"] = "reversal"
                result["track_2"]["score"] = reversal_score
                result["track_2"]["reason"] = f"见顶做空 {reversal_score}/6 触发"
            elif tf_score >= 4:
                result["track_2"]["sub_type"] = "trend_follow"
                result["track_2"]["score"] = tf_score
                result["track_2"]["reason"] = f"顺势做空 {tf_score}/6 触发（已错过完美顶部）"

        # ============================================================
        # 轨道3：暴跌反弹做多
        # 硬条件：日线非空头 + ADX ≥ 12（放宽，允许反弹行情）
        # ============================================================
        if trend_1d == "down":
            result["track_3"]["details"]["硬条件"] = f"❌ 1D趋势向下，禁止抄底"
        elif not adx_ok_reversal:
            result["track_3"]["details"]["硬条件"] = f"❌ ADX={adx:.1f} < 12"
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
                result["track_3"]["details"]["2.30m RSI超卖"] = f"✅ RSI={rsi:.1f}"
            else:
                result["track_3"]["details"]["2.30m RSI超卖"] = f"❌ RSI={rsi:.1f}"

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
                result["track_3"]["details"]["4.1H RSI超卖或底背离"] = f"✅ 1h RSI={rsi_1h if rsi_1h else 'N/A'}, 背离={rsi_div or '无'}"
            else:
                result["track_3"]["details"]["4.1H RSI超卖或底背离"] = f"❌ 1h RSI={rsi_1h if rsi_1h else 'N/A'}"

            if kdj_1h.get("j") is not None and kdj_1h["j"] < 0:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["5.1H KDJ超卖"] = f"✅ J={kdj_1h['j']:.1f} < 0"
            else:
                result["track_3"]["details"]["5.1H KDJ超卖"] = f"❌ J={kdj_1h.get('j', 'N/A')}"

            # 出现止跌形态
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

        # ============================================================
        # 轨道4：趋势回踩做多（新增）
        # 硬条件：日线多头 + 4H多头
        # ============================================================
        if trend_1d != "up" or trend_4h != "up":
            result["track_4"]["details"]["硬条件"] = f"❌ 需1D多头+4H多头（当前1D={trend_1d or '?'}, 4H={trend_4h or '?'}）"
        else:
            result["track_4"]["hard_ok"] = True
            result["track_4"]["details"]["硬条件"] = f"✅ 1D多头 + 4H多头"

            # 回踩到1H布林中轨附近
            boll_mid = boll_1h.get("mid")
            if boll_mid and abs(price - boll_mid) / boll_mid <= 0.02:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["1.回踩1H布林中轨"] = f"✅ 现价{price:.4f}，中轨{boll_mid:.4f}"
            else:
                result["track_4"]["details"]["1.回踩1H布林中轨"] = f"❌ 现价{price} 距中轨{boll_mid if boll_mid else 'N/A'}较远"

            # 缩量回踩
            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["volume"] < avg_vol * 0.8:
                    result["track_4"]["score"] += 1
                    result["track_4"]["details"]["2.缩量回踩"] = "✅ 成交量<前5根均量×0.8"
                else:
                    result["track_4"]["details"]["2.缩量回踩"] = "❌ 回踩未缩量"
            else:
                result["track_4"]["details"]["2.缩量回踩"] = "❌ 数据不足"

            # 出现止跌形态
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

            # 1H RSI在40-55之间（健康回踩区）
            if rsi_1h is not None and 40 <= rsi_1h <= 55:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["4.1H RSI健康"] = f"✅ RSI={rsi_1h:.1f}"
            else:
                result["track_4"]["details"]["4.1H RSI健康"] = f"❌ RSI={rsi_1h if rsi_1h else 'N/A'}"

            # 30m站上MA10
            if ma10 and price > ma10:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["5.站上30m MA10"] = f"✅ {price} > {ma10:.4f}"
            else:
                result["track_4"]["details"]["5.站上30m MA10"] = f"❌ 未站上MA10"

            # 4H MACD未死叉（趋势健康）
            if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None and macd_4h["dif"] >= macd_4h["dea"]:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["6.4H MACD健康"] = "✅ DIF >= DEA"
            else:
                result["track_4"]["details"]["6.4H MACD健康"] = "❌ 4H MACD已死叉"

        # ============================================================
        # 最终触发判定
        # ============================================================
        # 优先级：轨道2（做空）> 轨道4（回踩做多）> 轨道3（暴跌反弹）> 轨道1（底部突破）
        if result["track_2"]["hard_ok"] and result["track_2"].get("sub_type"):
            result["triggered"] = True
            result["direction"] = "short"
            result["reason"] = result["track_2"]["reason"]
            if is_spot_mode:
                result["direction"] = "spot_warning"
                result["reason"] = result["track_2"]["reason"].replace("做空", "逃顶预警")
        elif result["track_4"]["hard_ok"] and result["track_4"]["score"] >= 4:
            result["triggered"] = True
            result["direction"] = "long_pullback"
            result["reason"] = f"趋势回踩做多 {result['track_4']['score']}/6 触发"
        elif result["track_3"]["hard_ok"] and result["track_3"]["score"] >= 4:
            result["triggered"] = True
            result["direction"] = "long_rebound"
            result["reason"] = f"暴跌反弹 {result['track_3']['score']}/6 触发"
        elif result["track_1"]["hard_ok"] and result["track_1"]["score"] >= 4:
            result["triggered"] = True
            result["direction"] = "long_trend"
            result["reason"] = f"底部突破 {result['track_1']['score']}/6 触发"

        return result
