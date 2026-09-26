from strategies.base import BaseStrategy


class Strategy(BaseStrategy):
    version = "v1_default"
    name = "默认双轨策略"

    def evaluate(self, symbol, asset_type, market_data):
        result = {
            "track_a": {"score": 0, "details": {}},
            "track_b": {"score": 0, "details": {}},
            "triggered": False,
            "direction": None
        }

        # 提取数据
        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        funding = market_data.get("funding_rate")
        klines = market_data.get("klines", [])

        if price is None or rsi is None or rh is None or rl is None:
            result["track_a"]["details"]["数据状态"] = "数据不足，跳过"
            return result

        # ================= 轨道A：见顶做空 =================
        # A1: 价格逼近近期高点 (距离高点3%以内)
        if rh and price >= rh * 0.97:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A1-价格逼近近期高点"] = f"现价{price}，区间高点{rh}"
        else:
            result["track_a"]["details"]["A1-价格逼近近期高点"] = "未满足"

        # A2: RSI 超买 (>= 70)
        if rsi >= 70:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A2-RSI超买"] = f"RSI = {rsi:.1f}"
        else:
            result["track_a"]["details"]["A2-RSI超买"] = f"RSI = {rsi:.1f} (未超买)"

        # A3: 资金费率极端 (Hyperliquid 1小时结算，阈值调低为 0.05%)
        if funding and funding > 0.05:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A3-资金费率高位"] = f"费率 = {funding}%"
        else:
            result["track_a"]["details"]["A3-资金费率高位"] = f"费率 = {funding}% (未达阈值)"

        # A4: 技术面顶背离（简化判定：最近连续3根K线收阴）
        if klines and len(klines) >= 3:
            last_3 = klines[-3:]
            if all(k["close"] < k["open"] for k in last_3):
                result["track_a"]["score"] += 1
                result["track_a"]["details"]["A4-技术面走弱"] = "连续3根阴线"
            else:
                result["track_a"]["details"]["A4-技术面走弱"] = "未出现明显顶部形态"

        # ================= 轨道B：暴跌抄底 =================
        # B1: 价格大幅回撤 (从高点回撤 >= 15%)
        if rh and price <= rh * 0.85:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B1-价格大幅回撤"] = f"从高点{rh}回撤{((rh - price) / rh * 100):.1f}%"
        else:
            result["track_b"]["details"]["B1-价格大幅回撤"] = "未满足"

        # B2: RSI 超卖 (<= 30)
        if rsi <= 30:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B2-RSI超卖"] = f"RSI = {rsi:.1f}"
        else:
            result["track_b"]["details"]["B2-RSI超卖"] = f"RSI = {rsi:.1f}"

        # B3: 价格低于MA10
        if ma10 and price < ma10:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B3-价格低于MA10"] = f"现价{price}，MA10={ma10:.2f}"
        else:
            result["track_b"]["details"]["B3-价格低于MA10"] = "未满足"

        # ================= 触发判定 =================
        if result["track_a"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "short"
        elif result["track_b"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long"

        return result
