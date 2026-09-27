from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长强化版三轨策略"

    def evaluate(self, symbol, asset_type, market_data):
        result = {
            "track_1": {"score": 0, "details": {}, "hard_ok": False},
            "track_2": {"score": 0, "details": {}, "hard_ok": False},
            "track_3": {"score": 0, "details": {}, "hard_ok": False},
            "triggered": False, "direction": None, "reason": ""
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
            result["track_1"]["details"]["数据不足"] = "核心指标不足"
            return result

        # 通用硬条件：ADX < 20 全部静默
        if adx is not None and adx < 20:
            for t in ["track_1","track_2","track_3"]:
                result[t]["details"]["市场状态"] = f"ADX={adx:.2f}<20，静默"
            return result

        # 4H趋势
        trend_4h = None
        if klines_4h and len(klines_4h) >= 20:
            ma20_4h = sum(k["close"] for k in klines_4h[-20:]) / 20
            trend_4h = "up" if price > ma20_4h else "down"

        cvd_series = []
        if klines:
            cvd = 0
            for k in klines:
                cvd += k["volume"] * (1 if k["close"] >= k["open"] else -1)
                cvd_series.append(cvd)

        # 轨道1：底部突破做多
        if trend_4h == "down":
            result["track_1"]["details"]["硬条件-4H"] = "4H向下，禁止做多"
        else:
            result["track_1"]["hard_ok"] = True
            if price <= rl * 1.05:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.1-底部区域"] = f"现价{price}，低点{rl}"
            if ma10 and price > ma10:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.2-站上MA10"] = f"{price} > {ma10}"
            if 45 <= rsi <= 65:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.3-RSI温和"] = f"RSI={rsi:.1f}"
            if klines and len(klines) >= 10:
                recent_low_vol = min(k["volume"] for k in klines[-10:])
                if klines[-1]["volume"] > recent_low_vol * 1.5:
                    result["track_1"]["score"] += 1
                    result["track_1"]["details"]["1.4-放量确认"] = "底部放量"

        # 轨道2：见顶做空
        if trend_4h == "up":
            result["track_2"]["details"]["硬条件-4H"] = "4H向上，禁止做空"
        elif fp is not None and fp < 0.80:
            result["track_2"]["details"]["硬条件-费率"] = f"费率百分位{fp:.0%}<80%"
        else:
            result["track_2"]["hard_ok"] = True
            if price >= rh * 0.97:
                result["track_2"]["score"] += 1
                result["track_2"]["details"]["2.1-逼近高点"] = f"{price} 接近 {rh}"
            if rsi >= 70:
                result["track_2"]["score"] += 1
                result["track_2"]["details"]["2.2-RSI超买"] = f"RSI={rsi:.1f}"
            if cvd_series and len(cvd_series) >= 10:
                high_now = max(k["high"] for k in klines[-3:])
                high_prev = max(k["high"] for k in klines[-10:-3])
                cvd_now = cvd_series[-1]
                cvd_prev = max(cvd_series[-10:-3])
                if high_now > high_prev and cvd_now < cvd_prev * 0.95:
                    result["track_2"]["score"] += 1
                    result["track_2"]["details"]["2.3-CVD熊背离"] = "价新高CVD未新高"
            if fp is not None and fp >= 0.90:
                result["track_2"]["score"] += 1
                result["track_2"]["details"]["2.4-费率极值"] = f"百分位{fp:.0%}"

        # 轨道3：暴跌反弹做多
        if trend_4h == "down":
            result["track_3"]["details"]["硬条件-4H"] = "4H向下，禁止抄底"
        else:
            result["track_3"]["hard_ok"] = True
            if price <= rh * 0.85:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["3.1-大幅回撤"] = f"回撤{((rh-price)/rh*100):.1f}%"
            if rsi <= 35:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["3.2-RSI超卖"] = f"RSI={rsi:.1f}"
            if cvd_series and len(cvd_series) >= 10:
                low_now = min(k["low"] for k in klines[-3:])
                low_prev = min(k["low"] for k in klines[-10:-3])
                cvd_now = cvd_series[-1]
                cvd_prev = min(cvd_series[-10:-3])
                if low_now < low_prev and cvd_now > cvd_prev * 1.05:
                    result["track_3"]["score"] += 1
                    result["track_3"]["details"]["3.3-CVD牛背离"] = "价新低CVD回升"

        # 触发判定
        if result["track_2"]["hard_ok"] and result["track_2"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "short"
            result["reason"] = f"见顶做空 {result['track_2']['score']}/4"
        elif result["track_3"]["hard_ok"] and result["track_3"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_rebound"
            result["reason"] = f"暴跌反弹 {result['track_3']['score']}/3"
        elif result["track_1"]["hard_ok"] and result["track_1"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_trend"
            result["reason"] = f"底部突破 {result['track_1']['score']}/4"
        return result
