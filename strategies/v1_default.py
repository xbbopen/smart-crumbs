from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "双轨极端行情策略"

    def evaluate(self, symbol, asset_type, market_data):
        result = {"track_a": {"score": 0, "details": {}}, "track_b": {"score": 0, "details": {}}, "triggered": False, "direction": None}
        price, rsi, ma10 = market_data.get("current_price"), market_data.get("rsi"), market_data.get("ma10")
        rh, rl, funding = market_data.get("recent_high"), market_data.get("recent_low"), market_data.get("funding_rate")
        klines = market_data.get("klines", [])

        if price is None or rsi is None or rh is None: 
            result["track_a"]["details"]["数据状态"] = "数据不足"
            return result

        # ================= 轨道A：见顶做空 =================
        # A1: 逼近高点（距离高点3%以内）
        if price >= rh * 0.97:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A1-逼近高点"] = f"现价{price:.4f}，区间高点{rh:.4f}（距离仅{((rh-price)/rh*100):.1f}%）"
        else:
            result["track_a"]["details"]["A1-逼近高点"] = f"距离高点还有{((rh-price)/rh*100):.1f}%空间"

        # A2: RSI超买
        if rsi >= 70:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A2-RSI超买"] = f"RSI={rsi:.1f}（>=70，触发）"
        else:
            result["track_a"]["details"]["A2-RSI超买"] = f"RSI={rsi:.1f}（正常）"

        # A3: 币安资金费率过高
        if funding is not None and funding > 0.05:
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A3-资金费率"] = f"币安费率={funding:.4f}%（>0.05%，触发）"
        else:
            result["track_a"]["details"]["A3-资金费率"] = f"费率={funding:.4f}%（正常）" if funding is not None else "费率获取失败"

        # A4: 连续3根阴线
        if klines and len(klines) >= 3 and all(k["close"] < k["open"] for k in klines[-3:]):
            result["track_a"]["score"] += 1
            result["track_a"]["details"]["A4-形态走弱"] = "最近3根30m K线连续收阴"
        else:
            result["track_a"]["details"]["A4-形态走弱"] = "未出现明显顶部形态"

        # ================= 轨道B：暴跌抄底 =================
        # B1: 大幅回撤≥15%
        if price <= rh * 0.85:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B1-大幅回撤"] = f"从高点{rh:.4f}回撤{((rh-price)/rh*100):.1f}%（>=15%，触发）"
        else:
            result["track_b"]["details"]["B1-大幅回撤"] = f"回撤{((rh-price)/rh*100):.1f}%（未达标）"

        # B2: RSI超卖
        if rsi <= 30:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B2-RSI超卖"] = f"RSI={rsi:.1f}（<=30，触发）"
        else:
            result["track_b"]["details"]["B2-RSI超卖"] = f"RSI={rsi:.1f}"

        # B3: 跌破MA10
        if ma10 and price < ma10:
            result["track_b"]["score"] += 1
            result["track_b"]["details"]["B3-跌破MA10"] = f"现价{price:.4f} < MA10({ma10:.4f})，触发"
        else:
            result["track_b"]["details"]["B3-跌破MA10"] = "未跌破MA10"

        # 触发判定
        if result["track_a"]["score"] >= 3:
            result["triggered"], result["direction"] = True, "short"
        elif result["track_b"]["score"] >= 3:
            result["triggered"], result["direction"] = True, "long"
        return result
