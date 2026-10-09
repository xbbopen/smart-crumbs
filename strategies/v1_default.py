from strategies.base import BaseStrategy
from report_lexicon import trend_cn, momentum_cn, regime_cn


# ================= 币种分层（ATR 止损倍数） =================
MAJOR_COINS = {"BTC", "ETH"}
MID_COINS = {"SOL", "BNB", "XRP", "ADA", "AVAX", "LINK", "DOGE"}

# 🚀 新增：挂单有效性过滤阈值
# 偏离现价超过此比例的档位会被剔除（防止出现 RLC 那种 -65% 的虚拟挂单）
MAX_STAGE_DEVIATION = 0.20


def get_atr_multiplier(symbol):
    """按币种流动性/波动性返回 ATR 止损倍数。"""
    sym = (symbol or "").replace("_USDT", "").replace("_usdt", "").upper()
    if sym in MAJOR_COINS:
        return 1.5
    elif sym in MID_COINS:
        return 2.0
    else:
        return 2.5


class V1DefaultStrategy(BaseStrategy):
    version = "v1_default"
    name = "参谋长多周期共振策略"

    # ================= 4H 动能细分 =================
    def _calc_4h_momentum(self, md):
        ema20 = md.get("ema20_4h")
        ema50 = md.get("ema50_4h")
        rsi_4h = md.get("rsi_4h")
        macd_4h = md.get("macd_4h") or {}
        price = md.get("current_price")

        score = 0
        if ema20 and ema50:
            score += 1 if ema20 > ema50 else -1
        if ema20 and price:
            score += 1 if price > ema20 else -1
        if rsi_4h is not None:
            if rsi_4h > 55:
                score += 1
            elif rsi_4h < 45:
                score -= 1
        if macd_4h.get("dif") is not None and macd_4h.get("dea") is not None:
            score += 1 if macd_4h["dif"] > macd_4h["dea"] else -1

        if score >= 3:
            return "strong_bull"
        elif score >= 1:
            return "bull"
        elif score <= -3:
            return "strong_bear"
        elif score <= -1:
            return "bear"
        else:
            return "neutral"

    # ================= 动量停滞检测 =================
    def _detect_momentum_stall(self, md):
        macd_4h = md.get("macd_4h") or {}
        adx = md.get("adx")
        hist = macd_4h.get("hist")
        price = md.get("current_price")

        if hist is None or adx is None or not price or price <= 0:
            return False
        try:
            hist_pct = abs(float(hist)) / float(price)
            return hist_pct < 0.0005 and adx < 20
        except (TypeError, ValueError):
            return False

    # ================= 市场状态机 =================
    def _determine_regime(self, md):
        trend_1d = md.get("trend_1d")
        momentum_4h = self._calc_4h_momentum(md)

        if self._detect_momentum_stall(md):
            return ("momentum_stall",
                    ["track_3"],
                    ["track_1", "track_2", "track_4"],
                    "⏸️ 动量停滞：MACD 归零 + ADX 低位，主力按兵不动。仅允许暴跌反弹轨道")

        if trend_1d == "down" and momentum_4h in ("bear", "strong_bear"):
            return ("strong_bear",
                    ["track_2"],
                    ["track_1", "track_3", "track_4"],
                    "1D+4H双空头，只允许做空，禁止任何做多")

        if trend_1d == "down":
            return ("weak_bear",
                    ["track_2"],
                    ["track_1", "track_4"],
                    "1D空头，允许做空，禁止做多（轨道3有条件允许短线反弹）")

        if trend_1d == "up" and momentum_4h in ("bull", "strong_bull"):
            return ("strong_bull",
                    ["track_1", "track_4"],
                    ["track_2", "track_3"],
                    "1D+4H双多头，只允许做多，禁止任何做空")

        if trend_1d == "up":
            if momentum_4h in ("bear", "strong_bear"):
                return ("weak_bull_warning",
                        ["track_2"],
                        ["track_1", "track_3", "track_4"],
                        "⚠️ 1D多头但4H已转空，做多风险极高，只允许做空")
            else:
                return ("weak_bull",
                        ["track_4", "track_2"],
                        ["track_1", "track_3"],
                        "1D多头但4H震荡，只允许回踩做多或见顶做空")

        return ("ranging",
                ["track_2", "track_3"],
                ["track_1", "track_4"],
                "无明确趋势，短线双向操作，条件加严")

    # ================= 分档入场计划 =================
    def _build_entry_plan(self, symbol, direction, cp, atr, rh, rl, ma10, boll_mid, trigger_type):
        """
        生成分档入场计划。

        核心规则：
        1. 挂单价优先用阻力位（boll_mid / ma10），而非现价 cp
        2. 所有轨道通用：过滤掉偏离现价超过 MAX_STAGE_DEVIATION 的档位
        3. 过滤后权重保持不变（总仓变轻），重新计算加权平均价
        4. 止损统一基于 avg_price + atr_mult × ATR，并加 1.5% 保底距离
        """
        atr_mult = get_atr_multiplier(symbol)
        stages = []
        note = ""

        # ================= 各轨道挂单构建 =================
        if trigger_type == "long_trend":
            if atr and atr > 0:
                stages.append({"weight": 40, "type": "limit", "price": cp - 0.5 * atr,
                               "note": f"限价挂在 {cp - 0.5*atr:.4f}，等回踩"})
                stages.append({"weight": 35, "type": "limit", "price": cp - 1.0 * atr,
                               "note": f"限价挂在 {cp - 1.0*atr:.4f}"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.995, "note": "限价-0.5%"})
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.99, "note": "限价-1%"})
            if rl:
                stages.append({"weight": 25, "type": "limit", "price": rl * 1.005, "note": "近期低点上方"})
            else:
                stages.append({"weight": 25, "type": "limit", "price": cp * 0.98, "note": "限价-2%"})
            note = "底部突破后往往有回踩，限价单接飞刀"

        elif trigger_type == "short_reversal":
            stages.append({"weight": 70, "type": "market", "price": cp, "note": "顶部窗口极短，立即市价"})
            if rh:
                stages.append({"weight": 30, "type": "limit", "price": rh * 1.01, "note": "反弹到前高上方加仓"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 1.02, "note": "反弹+2%"})
            note = "见顶反转窗口极短，首档必须市价"

        elif trigger_type == "short_trend_follow":
            # 挂单价优先用阻力位，不被 cp 主导
            if boll_mid and boll_mid >= cp:
                anchor = boll_mid
                anchor_note = "1H布林中轨"
            elif ma10 and ma10 >= cp:
                anchor = ma10
                anchor_note = "30m MA10"
            else:
                # 价格已突破上方所有阻力位，只能往上加一点
                anchor = cp * 1.01
                anchor_note = "现价上方1%"

            stages.append({"weight": 60, "type": "limit", "price": anchor,
                           "note": f"限价挂在{anchor_note}"})
            stages.append({"weight": 40, "type": "limit", "price": anchor * 1.015,
                           "note": f"{anchor_note}上方1.5%"})
            note = "下跌中继的反弹很磨人，等反弹到阻力位挂限价空单"

        elif trigger_type == "long_rebound":
            if atr and atr > 0:
                stages.append({"weight": 35, "type": "limit", "price": cp - 0.5 * atr, "note": "限价-0.5ATR"})
                stages.append({"weight": 35, "type": "limit", "price": cp - 1.5 * atr, "note": "限价-1.5ATR"})
            else:
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.99, "note": "限价-1%"})
                stages.append({"weight": 35, "type": "limit", "price": cp * 0.97, "note": "限价-3%"})
            if rl:
                stages.append({"weight": 30, "type": "limit", "price": rl * 0.99, "note": "近期低点下方"})
            else:
                stages.append({"weight": 30, "type": "limit", "price": cp * 0.95, "note": "限价-5%"})
            note = "暴跌后往往有二次探底，全部限价，避免抄在半山腰"

        elif trigger_type == "long_pullback":
            stages.append({"weight": 60, "type": "market", "price": cp, "note": "已到回踩位，市价占位"})
            if boll_mid:
                stages.append({"weight": 40, "type": "limit", "price": boll_mid * 0.99,
                               "note": "布林中轨下方1%"})
            else:
                stages.append({"weight": 40, "type": "limit", "price": cp * 0.98, "note": "限价-2%"})
            note = "趋势中回踩到位即入场"

        # ================= 通用保护 1：过滤偏离过大的档位 =================
        if cp and cp > 0 and stages:
            valid_stages = [s for s in stages if abs(s["price"] - cp) / cp <= MAX_STAGE_DEVIATION]
            if valid_stages:
                stages = valid_stages
            else:
                # 极端情况：全部偏离过大，保留最接近现价的一档
                stages = [min(stages, key=lambda s: abs(s["price"] - cp))]

        # ================= 重新计算加权平均价（权重保持不变 = 总仓变轻） =================
        total_weight = sum(s["weight"] for s in stages)
        avg_price = (sum(s["price"] * s["weight"] for s in stages) / total_weight
                     if total_weight > 0 else cp)

        # ================= 通用保护 2：止损统一基于 avg_price =================
        is_long = bool(direction and direction.startswith("long"))

        if atr and atr > 0:
            stop = avg_price - atr_mult * atr if is_long else avg_price + atr_mult * atr
        else:
            # ATR 缺失时用固定 2% 兜底
            stop = avg_price * 0.98 if is_long else avg_price * 1.02

        # 保底：止损距 avg_price 至少 1.5%（防止 ATR 极小时贴脸）
        if is_long:
            max_allowed_stop = avg_price * 0.985
            if stop > max_allowed_stop:
                stop = max_allowed_stop
        else:
            min_allowed_stop = avg_price * 1.015
            if stop < min_allowed_stop:
                stop = min_allowed_stop

        return {"stages": stages, "avg_price": avg_price, "stop": stop, "note": note}

    # ================= 衍生品评分辅助 =================
    def _score_track1_derivatives(self, md, price, tr):
        score = 0
        max_score = 0

        oi_1h = md.get("oi_change_pct_1h")
        if oi_1h is not None:
            max_score += 1
            if oi_1h >= 1.0:
                score += 1
                tr["details"]["7.OI 1h增幅"] = f"✅ +{oi_1h:.2f}%（新多进场）"
            else:
                tr["details"]["7.OI 1h增幅"] = f"❌ {oi_1h:+.2f}%"
        else:
            tr["details"]["7.OI 1h增幅"] = "⚪ 数据不足"

        vwap = md.get("vwap_30m")
        if vwap and price:
            max_score += 1
            if price >= vwap:
                score += 1
                tr["details"]["8.站上VWAP"] = f"✅ 现价{price:.4f} ≥ VWAP {vwap:.4f}"
            else:
                tr["details"]["8.站上VWAP"] = f"❌ 现价{price:.4f} < VWAP {vwap:.4f}"
        else:
            tr["details"]["8.站上VWAP"] = "⚪ 数据不足"

        return score, max_score

    def _score_track2a_derivatives(self, md, price, tr):
        score = 0
        max_score = 0

        oi_1h = md.get("oi_change_pct_1h")
        if oi_1h is not None:
            max_score += 1
            if oi_1h <= -0.5:
                score += 1
                tr["details"]["7.OI 1h减幅"] = f"✅ {oi_1h:.2f}%（空头回补，见顶特征）"
            else:
                tr["details"]["7.OI 1h减幅"] = f"❌ {oi_1h:+.2f}%"
        else:
            tr["details"]["7.OI 1h减幅"] = "⚪ 数据不足"

        basis = md.get("basis_pct")
        vwap = md.get("vwap_30m")
        hit = False
        reason = []
        if basis is not None:
            max_score += 1
            if basis >= 0.05:
                hit = True
                reason.append(f"基差{basis:+.3f}%过热")
        if vwap and price:
            if max_score == 0:
                max_score += 1
            if price > vwap * 1.03:
                hit = True
                reason.append(f"高于VWAP{(price/vwap-1)*100:.1f}%")
        if max_score > 0:
            if hit:
                score += 1
                tr["details"]["8.基差/VWAP过热"] = f"✅ {' | '.join(reason)}"
            else:
                tr["details"]["8.基差/VWAP过热"] = "❌ 未见明显过热"
        else:
            tr["details"]["8.基差/VWAP过热"] = "⚪ 数据不足"

        return score, max_score

    def _score_track3_derivatives(self, md, price, tr):
        score = 0
        max_score = 0

        oi_1h = md.get("oi_change_pct_1h")
        fr = md.get("funding_rate")
        hit = False
        reason = []
        if oi_1h is not None:
            max_score += 1
            if oi_1h <= -3.0:
                hit = True
                reason.append(f"OI骤降{oi_1h:.2f}%（清算特征）")
        if fr is not None:
            if max_score == 0:
                max_score += 1
            if fr <= -0.01:
                hit = True
                reason.append(f"费率{fr:.4f}%极端负")
        if max_score > 0:
            if hit:
                score += 1
                tr["details"]["7.OI骤降/费率极端"] = f"✅ {' | '.join(reason)}"
            else:
                tr["details"]["7.OI骤降/费率极端"] = "❌ 未出现极端"
        else:
            tr["details"]["7.OI骤降/费率极端"] = "⚪ 数据不足"

        vwap = md.get("vwap_30m")
        if vwap and price:
            max_score += 1
            dev = (price - vwap) / vwap * 100
            if dev <= -3.0:
                score += 1
                tr["details"]["8.远离VWAP超卖"] = f"✅ 低于VWAP {dev:.2f}%"
            else:
                tr["details"]["8.远离VWAP超卖"] = f"❌ 偏离VWAP {dev:+.2f}%"
        else:
            tr["details"]["8.远离VWAP超卖"] = "⚪ 数据不足"

        return score, max_score

    def _score_track4_derivatives(self, md, price, tr):
        score = 0
        max_score = 0

        vwap = md.get("vwap_30m")
        if vwap and price:
            max_score += 1
            dev = (price - vwap) / vwap * 100
            if -1.0 <= dev <= 2.0:
                score += 1
                tr["details"]["7.VWAP支撑"] = f"✅ 距VWAP {dev:+.2f}%"
            else:
                tr["details"]["7.VWAP支撑"] = f"❌ 偏离VWAP {dev:+.2f}%"
        else:
            tr["details"]["7.VWAP支撑"] = "⚪ 数据不足"

        oi_4h = md.get("oi_change_pct_4h")
        if oi_4h is not None:
            max_score += 1
            if -1.0 <= oi_4h <= 3.0:
                score += 1
                tr["details"]["8.OI稳定"] = f"✅ 4h {oi_4h:+.2f}%"
            else:
                tr["details"]["8.OI稳定"] = f"❌ 4h {oi_4h:+.2f}%（异动）"
        else:
            tr["details"]["8.OI稳定"] = "⚪ 数据不足"

        return score, max_score

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

        adx_ok_trend_1 = adx is not None and adx >= 22
        adx_ok_trend_2 = adx is not None and adx >= 20
        adx_ok_reversal = adx is not None and adx >= 12
        adx_ok_pullback = adx is not None and adx >= 18

        cvd_series = []
        if klines:
            cvd = 0
            for k in klines:
                cvd += k["volume"] * (1 if k["close"] >= k["open"] else -1)
                cvd_series.append(cvd)

        # ================= 轨道1：底部突破做多 =================
        if not result["track_1"]["allowed"]:
            result["track_1"]["details"]["❌ 状态禁止"] = f"当前状态【{regime_cn(regime)}】禁止此轨道"
        elif rh and (rh - price) / rh < 0.10:
            result["track_1"]["details"]["硬条件"] = (
                f"❌ 距近期高点不足10%（当前距高{((rh-price)/rh*100):.1f}%），非底部区域"
            )
        elif trend_1d == "down" or trend_4h == "down":
            result["track_1"]["details"]["硬条件"] = f"❌ 趋势逆风（1D={trend_cn(trend_1d)}，4H={trend_cn(trend_4h)}）"
        elif not adx_ok_trend_1:
            adx_str_local = f"{adx:.1f}" if adx is not None else "N/A"
            result["track_1"]["details"]["硬条件"] = f"❌ ADX={adx_str_local} < 22（震荡市假突破多）"
        else:
            result["track_1"]["hard_ok"] = True
            result["track_1"]["details"]["硬条件"] = (
                f"✅ 1D={trend_cn(trend_1d)} + 4H={trend_cn(trend_4h)} + ADX≥22 + 距高点≥10%"
            )

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

            d_score, d_max = self._score_track1_derivatives(market_data, price, result["track_1"])
            result["track_1"]["score"] += d_score
            result["track_1"]["max"] = 6 + d_max

        # ================= 轨道2：做空 =================
        if not result["track_2"]["allowed"]:
            result["track_2"]["details"]["❌ 状态禁止"] = f"当前状态【{regime_cn(regime)}】禁止做空"
        else:
            if momentum_4h in ("bull", "strong_bull"):
                result["track_2"]["details"]["硬条件"] = "❌ 4H动能向上，禁止做空"
            elif not adx_ok_trend_2:
                adx_str_local = f"{adx:.1f}" if adx is not None else "N/A"
                result["track_2"]["details"]["硬条件"] = f"❌ ADX={adx_str_local} < 20"
            elif ma10 and price > ma10 * 1.02:
                result["track_2"]["details"]["硬条件"] = "❌ 价格偏离MA10超过2%，禁止追空"
            else:
                result["track_2"]["hard_ok"] = True
                result["track_2"]["details"]["硬条件"] = (
                    f"✅ 1D={trend_cn(trend_1d)} + 4H={momentum_cn(momentum_4h)} + ADX≥20"
                )

                result["track_2"]["details"]["──────── 📌 见顶做空（反转）────────"] = ""
                rev_score = 0
                rev_max = 6

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

                a4_hit = False
                a4_reasons = []
                if rsi_1h is not None and rsi_1h >= 70:
                    a4_hit = True
                    a4_reasons.append(f"1h RSI={rsi_1h_str}")
                if rsi_div == "bearish":
                    a4_hit = True
                    a4_reasons.append("1h顶背离")
                if fp is not None and fp > 0.5:
                    a4_hit = True
                    a4_reasons.append(f"费率拥挤度={fp:.1%}")
                if a4_hit:
                    rev_score += 1
                    result["track_2"]["details"]["A4.1H RSI超买 / 顶背离 / 费率极端"] = f"✅ {' | '.join(a4_reasons)}"
                else:
                    result["track_2"]["details"]["A4.1H RSI超买 / 顶背离 / 费率极端"] = f"❌ 1h RSI={rsi_1h_str}"

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

                d_score_2a, d_max_2a = self._score_track2a_derivatives(market_data, price, result["track_2"])
                rev_score += d_score_2a
                rev_max = 6 + d_max_2a

                result["track_2"]["details"]["📊 见顶做空得分"] = f"{rev_score}/{rev_max}"

                result["track_2"]["details"]["──────── 📉 顺势做空（趋势延续）────────"] = ""
                tf_core = 0
                tf_aux = 0
                tf_aux_max = 4

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

                oi_1h = market_data.get("oi_change_pct_1h")
                if oi_1h is not None:
                    tf_aux_max += 1
                    if oi_1h >= 0.5:
                        tf_aux += 1
                        result["track_2"]["details"]["B7.OI 1h增加"] = f"✅ {oi_1h:+.2f}%（空头加仓）"
                    else:
                        result["track_2"]["details"]["B7.OI 1h增加"] = f"❌ {oi_1h:+.2f}%"
                else:
                    result["track_2"]["details"]["B7.OI 1h增加"] = "⚪ 数据不足"

                vwap = market_data.get("vwap_30m")
                if vwap and price:
                    tf_aux_max += 1
                    dist_vwap = (price - vwap) / vwap
                    if -0.005 <= dist_vwap <= 0.03:
                        tf_aux += 1
                        result["track_2"]["details"]["B8.VWAP遇阻"] = f"✅ 距VWAP {dist_vwap*100:+.2f}%"
                    else:
                        result["track_2"]["details"]["B8.VWAP遇阻"] = f"❌ 偏离VWAP {dist_vwap*100:+.2f}%"
                else:
                    result["track_2"]["details"]["B8.VWAP遇阻"] = "⚪ 数据不足"

                result["track_2"]["details"]["📊 顺势做空核心"] = f"{tf_core}/2"
                result["track_2"]["details"]["📊 顺势做空辅助"] = f"{tf_aux}/{tf_aux_max}"

                if rev_score >= 5:
                    result["track_2"]["sub_type"] = "reversal"
                    result["track_2"]["score"] = rev_score
                    result["track_2"]["max"] = rev_max
                    result["track_2"]["reason"] = f"见顶做空 {rev_score}/{rev_max}"
                elif tf_core >= 2 and tf_aux >= 3:
                    result["track_2"]["sub_type"] = "trend_follow"
                    result["track_2"]["score"] = tf_core + tf_aux
                    result["track_2"]["max"] = 2 + tf_aux_max
                    result["track_2"]["reason"] = f"顺势做空 核心{tf_core}/2 + 辅助{tf_aux}/{tf_aux_max}"

        # ================= 轨道3：暴跌反弹做多 =================
        if not result["track_3"]["allowed"]:
            result["track_3"]["details"]["❌ 状态禁止"] = f"当前状态【{regime_cn(regime)}】禁止此轨道"
        elif trend_1d == "down":
            result["track_3"]["details"]["硬条件"] = "❌ 1D趋势向下，禁止抄底"
        elif not adx_ok_reversal:
            adx_str_local = f"{adx:.1f}" if adx is not None else "N/A"
            result["track_3"]["details"]["硬条件"] = f"❌ ADX={adx_str_local} < 12"
        elif momentum_4h in ("bear", "strong_bear"):
            extreme_oversold = (
                (rsi_4h is not None and rsi_4h < 20) and
                (rsi < 25) and
                (kdj_1h.get("j") is not None and kdj_1h["j"] < -10) and
                rsi_div == "bullish"
            )
            if not extreme_oversold:
                result["track_3"]["details"]["硬条件"] = (
                    f"❌ 4H动能={momentum_cn(momentum_4h)}，只有极端超卖才允许抄底"
                )
                result["track_3"]["details"]["🔒 4H下跌中继保护"] = "需 RSI_4H<20 + RSI_30m<25 + KDJ_1H J<-10 + RSI底背离 全部满足"
            else:
                result["track_3"]["hard_ok"] = True
                result["track_3"]["details"]["硬条件"] = f"✅ 4H下跌但已极端超卖，短线反弹机会"
                result["track_3"]["score"] = 6
                result["track_3"]["max"] = 6
        else:
            result["track_3"]["hard_ok"] = True
            result["track_3"]["details"]["硬条件"] = f"✅ 1D={trend_cn(trend_1d)} + ADX≥12"

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

                d_score_3, d_max_3 = self._score_track3_derivatives(market_data, price, result["track_3"])
                result["track_3"]["score"] += d_score_3
                result["track_3"]["max"] = 6 + d_max_3

        # ================= 轨道4：趋势回踩做多 =================
        if not result["track_4"]["allowed"]:
            result["track_4"]["details"]["❌ 状态禁止"] = f"当前状态【{regime_cn(regime)}】禁止此轨道"
        elif trend_1d != "up" or trend_4h != "up":
            result["track_4"]["details"]["硬条件"] = f"❌ 需1D多头+4H多头"
        elif not adx_ok_pullback:
            adx_str_local = f"{adx:.1f}" if adx is not None else "N/A"
            result["track_4"]["details"]["硬条件"] = f"❌ ADX={adx_str_local} < 18（震荡中无回踩可做）"
        else:
            result["track_4"]["hard_ok"] = True
            result["track_4"]["details"]["硬条件"] = "✅ 1D多头 + 4H多头 + ADX≥18"

            tolerance = 0.02
            if atr and price and price > 0:
                atr_pct = atr / price
                tolerance = max(0.01, min(atr_pct * 1.5, 0.04))

            if boll_mid and abs(price - boll_mid) / boll_mid <= tolerance:
                result["track_4"]["score"] += 1
                result["track_4"]["details"]["1.回踩1H布林中轨"] = (
                    f"✅ 现价{price:.4f}，中轨{boll_mid:.4f}，容差±{tolerance*100:.1f}%"
                )
            else:
                boll_mid_str = f"{boll_mid:.4f}" if boll_mid else "N/A"
                result["track_4"]["details"]["1.回踩1H布林中轨"] = (
                    f"❌ 距中轨{boll_mid_str}较远（当前容差±{tolerance*100:.1f}%）"
                )

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

            d_score_4, d_max_4 = self._score_track4_derivatives(market_data, price, result["track_4"])
            result["track_4"]["score"] += d_score_4
            result["track_4"]["max"] = 6 + d_max_4

        # ================= 最终裁决 =================
        candidates = []

        if result["track_2"]["hard_ok"] and result["track_2"].get("sub_type"):
            t2_max = result["track_2"].get("max", 6)
            t2_score = result["track_2"]["score"]
            threshold_2 = 5 if t2_max >= 8 else 4
            if t2_score >= threshold_2:
                candidates.append(("short", "track_2", t2_score, result["track_2"]["reason"]))

        if result["track_4"]["hard_ok"]:
            t4_max = result["track_4"].get("max", 6)
            t4_score = result["track_4"]["score"]
            threshold_4 = 5 if t4_max >= 8 else 4
            if t4_score >= threshold_4:
                candidates.append(("long_pullback", "track_4", t4_score, f"趋势回踩 {t4_score}/{t4_max}"))

        if result["track_3"]["hard_ok"]:
            t3_max = result["track_3"].get("max", 6)
            t3_score = result["track_3"]["score"]
            threshold_3 = 5 if t3_max >= 8 else 4
            if t3_score >= threshold_3:
                candidates.append(("long_rebound", "track_3", t3_score, f"暴跌反弹 {t3_score}/{t3_max}"))

        if result["track_1"]["hard_ok"]:
            t1_max = result["track_1"].get("max", 6)
            t1_score = result["track_1"]["score"]
            threshold_1 = 5 if t1_max >= 8 else 4
            if t1_score >= threshold_1:
                candidates.append(("long_trend", "track_1", t1_score, f"底部突破 {t1_score}/{t1_max}"))

        if candidates:
            priority_order = {"short": 4, "long_pullback": 3, "long_rebound": 2, "long_trend": 1}
            candidates.sort(key=lambda x: (priority_order.get(x[0], 0), x[2]), reverse=True)

            winner_dir, winner_track, winner_score, winner_reason = candidates[0]
            result["triggered"] = True
            result["direction"] = winner_dir
            result["reason"] = winner_reason

            if len(candidates) > 1:
                losers = [f"{c[1]}({c[3]})" for c in candidates[1:]]
                result["conflict_note"] = f"⚠️ 多轨道冲突！优先选择 [{winner_track}]，被否决：{', '.join(losers)}"

            trigger_type_map = {
                "short": "short_trend_follow" if result["track_2"]["sub_type"] == "trend_follow" else "short_reversal",
                "long_pullback": "long_pullback",
                "long_rebound": "long_rebound",
                "long_trend": "long_trend"
            }
            tt = trigger_type_map.get(winner_dir, "long_trend")
            result["entry_plan"] = self._build_entry_plan(
                symbol, winner_dir, price, atr, rh, rl, ma10, boll_mid, tt
            )

            if is_spot_mode and winner_dir == "short":
                result["direction"] = "spot_warning"
                result["reason"] = "现货逃顶预警（" + winner_reason + "）"
                result["entry_plan"] = None

        return result
