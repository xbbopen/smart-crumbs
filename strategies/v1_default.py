from strategies.base import BaseStrategy


class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "三轨并行趋势策略"

    def evaluate(self, symbol, asset_type, market_data):
        # 初始化三轨结果
        result = {
            "track_1": {"score": 0, "details": {}},  # 底部突破做多
            "track_2": {"score": 0, "details": {}},  # 见顶做空
            "track_3": {"score": 0, "details": {}},  # 暴跌反弹做多
            "triggered": False,
            "direction": None
        }

        # 提取基础数据
        price = market_data.get("current_price")
        rsi = market_data.get("rsi")
        ma10 = market_data.get("ma10")
        rh = market_data.get("recent_high")
        rl = market_data.get("recent_low")
        funding = market_data.get("funding_rate")
        klines = market_data.get("klines", [])

        # 数据不足则提前返回
        if price is None or rsi is None or rh is None or rl is None:
            result["track_1"]["details"]["数据状态"] = "核心指标不足，跳过评估"
            return result

        # ================= 轨道1：底部震荡突破（做多） =================
        # 1.1 价格处于近期低点附近（区间下半区）
        if price <= rl * 1.05:
            result["track_1"]["score"] += 1
            result["track_1"]["details"]["1.1-底部区域"] = f"现价{price:.4f}，近期低点{rl:.4f}"
        else:
            result["track_1"]["details"]["1.1-底部区域"] = f"价格高于底部区域"

        # 1.2 站上MA10
        if ma10 and price > ma10:
            result["track_1"]["score"] += 1
            result["track_1"]["details"]["1.2-站上MA10"] = f"现价{price:.4f} > MA10({ma10:.4f})"
        else:
            result["track_1"]["details"]["1.2-站上MA10"] = "未站上MA10"

        # 1.3 RSI 处于温和上升区（非超买、非超卖）
        if 45 <= rsi <= 65:
            result["track_1"]["score"] += 1
            result["track_1"]["details"]["1.3-RSI温和"] = f"RSI={rsi:.1f}（处于温和上升区间）"
        else:
            result["track_1"]["details"]["1.3-RSI温和"] = f"RSI={rsi:.1f}（不满足）"

        # 1.4 成交量放大（最近1根K线成交量大于前5根均值1.2倍）
        if klines and len(klines) >= 6:
            avg_vol = sum(k["volume"] for k in klines[-6:-1]) / 5
            if klines[-1]["volume"] > avg_vol * 1.2:
                result["track_1"]["score"] += 1
                result["track_1"]["details"]["1.4-成交量放大"] = "放量突破"
            else:
                result["track_1"]["details"]["1.4-成交量放大"] = "量能未明显放大"
        else:
            result["track_1"]["details"]["1.4-成交量放大"] = "K线数据不足"

        # ================= 轨道2：见顶做空 =================
        # 2.1 逼近近期高点（3%以内）
        if price >= rh * 0.97:
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.1-逼近高点"] = f"现价{price:.4f}，高点{rh:.4f}"
        else:
            result["track_2"]["details"]["2.1-逼近高点"] = f"距离高点还有{((rh-price)/rh*100):.1f}%空间"

        # 2.2 RSI超买
        if rsi >= 70:
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.2-RSI超买"] = f"RSI={rsi:.1f}（超买）"
        else:
            result["track_2"]["details"]["2.2-RSI超买"] = f"RSI={rsi:.1f}"

        # 2.3 资金费率过高（monitor.py 已统一为百分比形式，如 0.05 代表 0.05%）
        if funding is not None and funding > 0.05:
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.3-资金费率"] = f"费率={funding:.4f}%（高位）"
        else:
            result["track_2"]["details"]["2.3-资金费率"] = f"费率={funding if funding is not None else '获取失败'}"

        # 2.4 形态走弱（连续3根阴线）
        if klines and len(klines) >= 3 and all(k["close"] < k["open"] for k in klines[-3:]):
            result["track_2"]["score"] += 1
            result["track_2"]["details"]["2.4-形态走弱"] = "连续3根30m K线收阴"
        else:
            result["track_2"]["details"]["2.4-形态走弱"] = "未见明显顶部形态"

        # ================= 轨道3：暴跌后强势反弹（做多） =================
        # 3.1 大幅回撤 >= 15%
        if price <= rh * 0.85:
            result["track_3"]["score"] += 1
            result["track_3"]["details"]["3.1-大幅回撤"] = f"从高点{rh:.4f}回撤{((rh-price)/rh*100):.1f}%"
        else:
            result["track_3"]["details"]["3.1-大幅回撤"] = f"回撤{((rh-price)/rh*100):.1f}%（未达标）"

        # 3.2 RSI超卖
        if rsi <= 35:
            result["track_3"]["score"] += 1
            result["track_3"]["details"]["3.2-RSI超卖"] = f"RSI={rsi:.1f}（超卖）"
        else:
            result["track_3"]["details"]["3.2-RSI超卖"] = f"RSI={rsi:.1f}"

        # 3.3 企稳信号（站上MA10 或 出现长下影线/锤头线）
        if ma10 and price > ma10:
            result["track_3"]["score"] += 1
            result["track_3"]["details"]["3.3-企稳信号"] = f"现价{price:.4f} > MA10({ma10:.4f})"
        elif klines and len(klines) >= 1:
            last = klines[-1]
            if (last["close"] - last["low"]) > (last["high"] - last["close"]) * 1.5:
                result["track_3"]["score"] += 1
                result["track_3"]["details"]["3.3-企稳信号"] = "出现长下影线（锤头线）"
            else:
                result["track_3"]["details"]["3.3-企稳信号"] = "未出现明显企稳形态"
        else:
            result["track_3"]["details"]["3.3-企稳信号"] = "无K线数据"

        # ================= 最终触发判定 =================
        # 轨道2（做空）优先级最高；若无，则看轨道1（底部趋势做多）；再若无，看轨道3（暴跌反弹做多）
        if result["track_2"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "short"
        elif result["track_1"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_trend"
        elif result["track_3"]["score"] >= 3:
            result["triggered"] = True
            result["direction"] = "long_rebound"

        return result
