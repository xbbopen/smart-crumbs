from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "默认双轨策略"

    def evaluate(self, symbol, asset_type, market_data):
        result = {"track_a": {"score": 0, "details": {}}, "track_b": {"score": 0, "details": {}}, "triggered": False, "direction": None}
        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        funding = market_data.get("funding_rate")
        klines = market_data.get("klines", [])

        if price is None or rsi is None or rh is None or rl is None:
            result["track_a"]["details"]["数据状态"] = "数据不足"
            return result

        # ================= 轨道A：见顶做空 =================
        if rh and price >= rh * 0.97:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A1-逼近高点"] = f"现价{price}，高点{rh}"
        else:
            result["track_a"]["details"]["A1-逼近高点"] = "未满足"

        if rsi >= 70:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A2-RSI超买"] = f"RSI={rsi:.1f}"
        else:
            result["track_a"]["details"]["A2-RSI超买"] = f"RSI={rsi:.1f}"

        if funding and funding > 0.05:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A3-资金费率"] = f"费率={funding}%"
        else:
            result["track_a"]["details"]["A3-资金费率"] = f"费率={funding}%"

        if klines and len(klines) >= 3 and all(k["close"] < k["open"] for k in klines[-3:]):
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A4-形态走弱"] = "连续3根阴线"
        else:
            result["track_a"]["details"]["A4-形态走弱"] = "未出现明显顶部"

        # ================= 轨道B：暴跌抄底 =================
        if rh and price <= rh * 0.85:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B1-大幅回撤"] = f"回撤{((rh-price)/rh*100):.1f}%"
        else:
            result["track_b"]["details"]["B1-大幅回撤"] = "未满足"

        if rsi <= 30:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B2-RSI超卖"] = f"RSI={rsi:.1f}"
        else:
            result["track_b"]["details"]["B2-RSI超卖"] = f"RSI={rsi:.1f}"

        if ma10 and price < ma10:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B3-低于MA10"] = f"现价{price}，MA10={ma10:.2f}"
        else:
            result["track_b"]["details"]["B3-低于MA10"] = "未满足"

        # 触发判定
        if result["track_a"]["score"] >= 3:
            result["triggered"], result["direction"] = True, "short"
        elif result["track_b"]["score"] >= 3:
            result["triggered"], result["direction"] = True, "long"
        return result
