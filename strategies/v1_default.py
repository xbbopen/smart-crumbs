from strategies.base import BaseStrategy

class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "三轨并行趋势策略"

    def evaluate(self, symbol, asset_type, market_data):
        result = {
            "track_1": {"score": 0, "details": {}},  # 底部突破做多
            "track_2": {"score": 0, "details": {}},  # 见顶做空
            "track_3": {"score": 0, "details": {}},  # 暴跌反弹做多
            "triggered": False,
            "direction": None,
            "reason": ""
        }

        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        funding = market_data.get("funding_rate")
        klines = market_data.get("klines", [])

        if price is None or rsi is None or rh is None or rl is None:
            result["track_1"]["details"]["数据状态"] = "核心指标不足，跳过评估"
            return result

        # ================= 【底部突破做多】 =================
        if price <= rl * 1.05:
            result["track_1"]["score"] += 1
            result["track_1"]["details"]["1.1-底部区域"] = f"现价{price:.4f}，近期低点{rl:.4f} (处于底部1.05倍范围内)"
        else:
            result["track_1"]["details"]["1.1-底部区域"] = f"现价{price:.4f} 高于底部区域 (近期低点{rl:.4f})"

        if ma10 and price > ma10:
            result["track_1"]["score"] += 1
            result["track_1"]["details"]["1.2-站上MA10"] = f"现价{price:.4f} > MA10({ma10:.4f})"
        else:
            result["track_1"]["details"]["1.2-站上MA10"] = "未站上MA10" if ma10 else "MA10无法计算"

        if 45 <= rsi <= 65:
            result["track_1"]["score"] += 1
            result["track_1"]["details"]["1.3-RSI温和"] = f"RSI={rsi:.1f} (处于45-65温和上升区间)"
        else:
            result["track_1"]["details"]["1.3-RSI温和"] = f"RSI={rsi:.1f} (不在45-65区间)"

        if klines and len(klines) >= 6:
            avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
            if klines[-1]["volume"] > avg_vol * 1.2:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.4-成交量放大"] = f"当前成交量{klines[-1]['volume']:.0f}，前5根均量{avg_vol:.0f} (放大超1.2倍)"
            else:
                result["track_1"]["details"]["1.4-成交量放大"] = "量能未明显放大"
        else:
            result["track_1"]["details"]["1.4-成交量放大"] = "K线数据不足"

        # ================= 【见顶做空】 =================
        if price >= rh * 0.97:
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.1-逼近高点"] = f"现价{price:.4f}，近期高点{rh:.4f} (距离{(rh-price)/rh*100:.1f}%)"
        else:
            result["track_2"]["details"]["2.1-逼近高点"] = f"距离高点还有{((rh-price)/rh*100):.1f}%空间"

        if rsi >= 70:
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.2-RSI超买"] = f"RSI={rsi:.1f} (>=70 超买)"
        else:
            result["track_2"]["details"]["2.2-RSI超买"] = f"RSI={rsi:.1f} (未达70超买)"

        if funding is not None and funding > 0.05:
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.3-资金费率"] = f"费率={funding:.4f}% (>0.05% 多头拥挤)"
        else:
            result["track_2"]["details"]["2.3-资金费率"] = f"费率={funding:.4f}% (未达0.05%阈值)" if funding is not None else "费率获取失败(现货或无数据)"

        if klines and len(klines) >= 3 and all(k["close"] < k["open"] for k in klines[-3:]):
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.4-形态走弱"] = "最近3根30m K线连续收阴"
        else:
            result["track_2"]["details"]["2.4-形态走弱"] = "未出现连续3根阴线"

        # ================= 【暴跌反弹做多】 =================
        if price <= rh * 0.85:
            result["track_3"]["score"] += 1
            result["track_3"]["details"]["3.1-大幅回撤"] = f"从高点{rh:.4f}回撤{((rh-price)/rh*100):.1f}% (>=15%)"
        else:
            result["track_3"]["details"]["3.1-大幅回撤"] = f"回撤{((rh-price)/rh*100):.1f}% (未达15%阈值)"

        if rsi <= 35:
            result["track_3"]["score"] += 1
            result["track_3"]["details"]["3.2-RSI超卖"] = f"RSI={rsi:.1f} (<=35 超卖)"
        else:
            result["track_3"]["details"]["3.2-RSI超卖"] = f"RSI={rsi:.1f} (未达35超卖)"

        if ma10 and price > ma10:
            result["track_3"]["score"] += 1
            result["track_3"]["details"]["3.3-企稳信号"] = f"现价{price:.4f} > MA10({ma10:.4f})"
        elif klines and len(klines) >= 1:
            last = klines[-1]
            if (last["close"] - last["low"]) > (last["high"] - last["close"]) * 1.5:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["3.3-企稳信号"] = "出现长下影线（锤头线）"
            else:
                result["track_3"]["details"]["3.3-企稳信号"] = "未出现长下影线或站上MA10"
        else:
            result["track_3"]["details"]["3.3-企稳信号"] = "无K线数据"

        # ================= 最终触发判定与原因说明 =================
        if result["track_2"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "short"
            met_conditions = [k for k, v in result["track_2"]["details"].items() if any(word in v for word in ["逼近", "超买", ">0.05", "连续"])]
            result["reason"] = f"满足见顶做空条件 {result['track_2']['score']}/4，触发项：{'、'.join(met_conditions)}"
        elif result["track_1"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_trend"
            met_conditions = [k for k, v in result["track_1"]["details"].items() if any(word in v for word in ["底部", "> MA10", "45-65", "放大"])]
            result["reason"] = f"满足底部突破做多条件 {result['track_1']['score']}/4，触发项：{'、'.join(met_conditions)}"
        elif result["track_3"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_rebound"
            met_conditions = [k for k, v in result["track_3"]["details"].items() if any(word in v for word in ["回撤", "超卖", "> MA10", "下影线"])]
            result["reason"] = f"满足暴跌反弹做多条件 {result['track_3']['score']}/3，触发项：{'、'.join(met_conditions)}"

        return result
