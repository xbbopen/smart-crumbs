from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长强化版三轨策略"

    def evaluate(self, symbol, asset_type, market_data):
        is_spot_mode = market_data.get("data_mode") == "spot"

        result = {
            "track_1": {"score": 0, "details": {}, "hard_ok": False},
            "track_2": {"score": 0, "details": {}, "hard_ok": False, "sub_type": None, "reason": ""},
            "track_3": {"score": 0, "details": {}, "hard_ok": False},
            "triggered": False, "direction": None, "reason": "",
            "data_mode": "spot" if is_spot_mode else "futures"
        }

        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        funding = market_data.get("funding_rate")
        adx = market_data.get("adx")
        klines = market_data.get("klines", [])
        klines_4h = market_data.get("klines_4h", [])
        fp = market_data.get("funding_percentile")

        if price is None or rsi is None or rh is None or rl is None:
            result["track_1"]["details"]["数据不足"] = "❌ 核心指标数据缺失，无法评估"
            return result

        # 通用硬条件：ADX < 20 全部静默
        if adx is not None and adx < 20:
            for t in ["track_1", "track_2", "track_3"]:
                result[t]["details"]["市场状态"] = f"❌ ADX={adx:.2f} < 20（市场无序震荡，策略强制静默）"
            return result

        # 4H趋势
        trend_4h = None
        if klines_4h and len(klines_4h) >= 20:
            ma20_4h = sum(k["close"] for k in klines_4h[-20:]) / 20
            trend_4h = "up" if price > ma20_4h else "down"

        if is_spot_mode:
            result["track_1"]["details"]["数据模式"] = "🟢 现货分析模式（无资金费率、无OI）"
            result["track_2"]["details"]["数据模式"] = "🟢 现货分析模式（做空降级为逃顶预警）"
            result["track_3"]["details"]["数据模式"] = "🟢 现货分析模式（无资金费率、无OI）"

        # 计算 CVD 序列
        cvd_series = []
        if klines:
            cvd = 0
            for k in klines:
                cvd += k["volume"] * (1 if k["close"] >= k["open"] else -1)
                cvd_series.append(cvd)

        # ================= 轨道1：底部突破做多 =================
        if trend_4h == "down":
            result["track_1"]["details"]["硬条件-4H趋势"] = "❌ 4H趋势向下，禁止做多"
        else:
            result["track_1"]["hard_ok"] = True
            result["track_1"]["details"]["硬条件-4H趋势"] = "✅ 4H趋势向上或震荡，允许做多"

            if price <= rl * 1.05:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.1-底部区域"] = f"✅ 已满足: 现价{price}，低点{rl}"
            else:
                result["track_1"]["details"]["1.1-底部区域"] = f"❌ 未满足: 现价{price} 高于低点{rl}的1.05倍"

            if ma10 and price > ma10:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.2-站上MA10"] = f"✅ 已满足: {price} > MA10({ma10})"
            else:
                result["track_1"]["details"]["1.2-站上MA10"] = f"❌ 未满足: 现价{price} < MA10({ma10})"

            if 45 <= rsi <= 65:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.3-RSI温和"] = f"✅ 已满足: RSI={rsi:.1f} 在45-65区间"
            else:
                result["track_1"]["details"]["1.3-RSI温和"] = f"❌ 未满足: RSI={rsi:.1f} 不在45-65区间"

            if klines and len(klines) >= 10:
                recent_low_vol = min(k["volume"] for k in klines[-10:])
                if klines[-1]["volume"] > recent_low_vol * 1.5:
                    result["track_1"]["score"] += 1
                    result["track_1"]["details"]["1.4-放量确认"] = "✅ 已满足: 底部出现显著放量"
                else:
                    result["track_1"]["details"]["1.4-放量确认"] = "❌ 未满足: 成交量未放大1.5倍"
            else:
                result["track_1"]["details"]["1.4-放量确认"] = "❌ 数据不足，无法判断"

        # ================= 轨道2：做空（见顶做空 + 顺势做空） =================
        if trend_4h == "up":
            result["track_2"]["details"]["硬条件-4H趋势"] = "❌ 4H趋势向上，禁止做空"
        else:
            result["track_2"]["hard_ok"] = True
            result["track_2"]["details"]["硬条件-4H趋势"] = "✅ 4H趋势向下或震荡，允许做空"

            # ========================================
            # A. 见顶做空（反转）—— 满分4分，≥3分触发
            # ========================================
            result["track_2"]["details"]["──────── 📌 见顶做空（反转）────────"] = ""
            reversal_score = 0

            # A1 逼近高点
            if price >= rh * 0.97:
                reversal_score += 1
                result["track_2"]["details"]["A1-逼近高点"] = f"✅ 已满足: {price} 接近 {rh}"
            else:
                result["track_2"]["details"]["A1-逼近高点"] = f"❌ 未满足: 距离高点还有{((rh-price)/rh*100):.1f}%"

            # A2 RSI超买
            if rsi >= 70:
                reversal_score += 1
                result["track_2"]["details"]["A2-RSI超买"] = f"✅ 已满足: RSI={rsi:.1f} ≥ 70"
            else:
                result["track_2"]["details"]["A2-RSI超买"] = f"❌ 未满足: RSI={rsi:.1f} < 70"

            # A3 CVD熊背离
            if cvd_series and len(cvd_series) >= 10:
                high_now = max(k["high"] for k in klines[-3:])
                high_prev = max(k["high"] for k in klines[-10:-3])
                cvd_now = cvd_series[-1]
                cvd_prev = max(cvd_series[-10:-3])
                if high_now > high_prev and cvd_now < cvd_prev * 0.95:
                    reversal_score += 1
                    result["track_2"]["details"]["A3-CVD熊背离"] = "✅ 已满足: 价新高CVD未新高"
                else:
                    result["track_2"]["details"]["A3-CVD熊背离"] = "❌ 未满足: 价格与CVD未出现顶背离"
            else:
                result["track_2"]["details"]["A3-CVD熊背离"] = "❌ 数据不足"

            # A4 费率拥挤
            if is_spot_mode:
                result["track_2"]["details"]["A4-费率极值"] = "🟢 现货模式，不适用"
            elif fp is not None and fp >= 0.80:
                reversal_score += 1
                result["track_2"]["details"]["A4-费率极值"] = f"✅ 已满足: 拥挤度{fp:.0%} ≥ 80%"
            else:
                result["track_2"]["details"]["A4-费率极值"] = f"❌ 未满足: 拥挤度{fp if fp is not None else 0:.0%} < 80%"

            # ========================================
            # B. 顺势做空（趋势延续）—— 满分4分，≥3分触发
            # ========================================
            result["track_2"]["details"]["──────── 📉 顺势做空（趋势）────────"] = ""
            trend_follow_score = 0

            # B1 已跌破MA10
            if ma10 and price < ma10:
                trend_follow_score += 1
                result["track_2"]["details"]["B1-跌破MA10"] = f"✅ 已满足: {price} < MA10({ma10})"
            else:
                result["track_2"]["details"]["B1-跌破MA10"] = f"❌ 未满足: {price} 仍在 MA10({ma10}) 上方"

            # B2 回撤区间（5%~15%）
            drawdown = (rh - price) / rh if rh else 0
            if 0.05 <= drawdown <= 0.15:
                trend_follow_score += 1
                result["track_2"]["details"]["B2-回撤区间"] = f"✅ 已满足: 距高点回撤{drawdown*100:.1f}%（5%-15%区间）"
            elif drawdown < 0.05:
                result["track_2"]["details"]["B2-回撤区间"] = f"❌ 未满足: 仅回撤{drawdown*100:.1f}%，距顶部太近（应看A类见顶信号）"
            else:
                result["track_2"]["details"]["B2-回撤区间"] = f"❌ 未满足: 已回撤{drawdown*100:.1f}%，进入暴跌反弹区（>15%）"

            # B3 反弹遇阻（最近出现长上影或看跌吞没）
            if klines and len(klines) >= 2:
                last = klines[-1]
                prev = klines[-2]
                body_last = abs(last["close"] - last["open"])
                upper_shadow_last = last["high"] - max(last["close"], last["open"])
                is_bearish_engulf = (prev["close"] > prev["open"] and last["close"] < last["open"]
                                     and last["open"] >= prev["close"] and last["close"] <= prev["open"])
                if body_last > 0 and upper_shadow_last > body_last * 1.5:
                    trend_follow_score += 1
                    result["track_2"]["details"]["B3-反弹遇阻"] = "✅ 已满足: 出现长上影线（抛压明显）"
                elif is_bearish_engulf:
                    trend_follow_score += 1
                    result["track_2"]["details"]["B3-反弹遇阻"] = "✅ 已满足: 出现看跌吞没"
                else:
                    result["track_2"]["details"]["B3-反弹遇阻"] = "❌ 未满足: 未出现明显遇阻形态"
            else:
                result["track_2"]["details"]["B3-反弹遇阻"] = "❌ 数据不足"

            # B4 放量下跌
            if klines and len(klines) >= 6:
                avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
                if klines[-1]["close"] < klines[-1]["open"] and klines[-1]["volume"] > avg_vol * 1.3:
                    trend_follow_score += 1
                    result["track_2"]["details"]["B4-放量下跌"] = "✅ 已满足: 阴线放量（卖压真实）"
                else:
                    result["track_2"]["details"]["B4-放量下跌"] = "❌ 未满足: 未出现放量阴线"
            else:
                result["track_2"]["details"]["B4-放量下跌"] = "❌ 数据不足"

            # ========================================
            # 轨道2 最终判定：见顶做空优先，顺势做空兜底
            # ========================================
            result["track_2"]["details"]["📊 见顶做空得分"] = f"{reversal_score}/4"
            result["track_2"]["details"]["📊 顺势做空得分"] = f"{trend_follow_score}/4"

            if reversal_score >= 3:
                result["track_2"]["sub_type"] = "reversal"
                result["track_2"]["score"] = reversal_score
                result["track_2"]["reason"] = f"【见顶做空】反转条件 {reversal_score}/4 触发"
            elif trend_follow_score >= 3:
                result["track_2"]["sub_type"] = "trend_follow"
                result["track_2"]["score"] = trend_follow_score
                result["track_2"]["reason"] = f"【顺势做空】趋势条件 {trend_follow_score}/4 触发（已错过完美见顶时机，下跌中继做空）"

        # ================= 轨道3：暴跌反弹做多 =================
        if trend_4h == "down":
            result["track_3"]["details"]["硬条件-4H趋势"] = "❌ 4H趋势向下，禁止抄底"
        else:
            result["track_3"]["hard_ok"] = True
            result["track_3"]["details"]["硬条件-4H趋势"] = "✅ 4H趋势向上或震荡，允许抄底"

            if price <= rh * 0.85:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["3.1-大幅回撤"] = f"✅ 已满足: 回撤{((rh-price)/rh*100):.1f}% ≥ 15%"
            else:
                result["track_3"]["details"]["3.1-大幅回撤"] = f"❌ 未满足: 当前回撤{((rh-price)/rh*100):.1f}% < 15%"

            if rsi <= 35:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["3.2-RSI超卖"] = f"✅ 已满足: RSI={rsi:.1f} ≤ 35"
            else:
                result["track_3"]["details"]["3.2-RSI超卖"] = f"❌ 未满足: RSI={rsi:.1f} > 35"

            if cvd_series and len(cvd_series) >= 10:
                low_now = min(k["low"] for k in klines[-3:])
                low_prev = min(k["low"] for k in klines[-10:-3])
                cvd_now = cvd_series[-1]
                cvd_prev = min(cvd_series[-10:-3])
                if low_now < low_prev and cvd_now > cvd_prev * 1.05:
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["3.3-CVD牛背离"] = "✅ 已满足: 价新低CVD回升"
                else:
                    result["track_3"]["details"]["3.3-CVD牛背离"] = "❌ 未满足: 价格与CVD未出现底背离"
            else:
                result["track_3"]["details"]["3.3-CVD牛背离"] = "❌ 数据不足，无法判断"

        # ================= 最终触发判定 =================
        # 优先级：轨道2（做空）> 轨道3（反弹做多）> 轨道1（底部突破）
        if result["track_2"]["hard_ok"] and result["track_2"].get("sub_type"):
            result["triggered"] = True
            result["direction"] = "short"
            result["reason"] = result["track_2"]["reason"]
            if is_spot_mode:
                result["direction"] = "spot_warning"
                result["reason"] = result["track_2"]["reason"].replace("做空", "逃顶预警")

        elif result["track_3"]["hard_ok"] and result["track_3"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_rebound"
            result["reason"] = f"暴跌反弹条件 {result['track_3']['score']}/3 触发"

        elif result["track_1"]["hard_ok"] and result["track_1"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_trend"
            result["reason"] = f"底部突破条件 {result['track_1']['score']}/4 触发"

        return result
