# -*- coding: utf-8 -*-
"""
v1_default：默认双轨信号策略。

轨道 A（做空/逃顶）：见顶信号
轨道 B（抄底）：暴跌抄底信号

不同类型的标的：
- futures：完整双轨（K线/资金费率/OI/多空比/ATR/MA10）
- spot  ：仅技术面做多。轨道 A 只输出"逃顶预警（建议减仓）"，不触发做空。
         轨道 B 正常输出（只用技术子信号，跳过杠杆类信号）。
- alpha ：仅轨道 B 技术面（回撤/RSI超卖/止跌形态），忽略杠杆类信号。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import (
    BaseStrategy, atr, closes_list, current_price, has_bottom_pattern,
    has_top_pattern, highest, is_bearish_engulfing, is_bullish_engulfing,
    is_hammer, is_higher_high_but_lower_indicator,
    is_lower_low_but_higher_indicator, lowest, macd, rsi, sma,
)


class V1DefaultStrategy(BaseStrategy):
    name = "v1_default"

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _candles(self, md: Dict[str, Any]) -> List[Dict[str, float]]:
        return md.get("candles") or []

    def _closes(self, md: Dict[str, Any]) -> List[float]:
        return closes_list(self._candles(md))

    def _full_indicators(self, closes: List[float], rsi_period: int):
        r = rsi(closes, rsi_period)
        m_line, m_sig, m_hist = macd(closes)
        rsi_vals = None
        if len(closes) >= rsi_period + 2:
            # 生成一条 RSI 序列用于背离判断
            tmp: List[float] = []
            for i in range(rsi_period + 1, len(closes) + 1):
                v = rsi(closes[:i], rsi_period)
                if v is not None:
                    tmp.append(v)
            rsi_vals = tmp
        return r, m_line, m_sig, m_hist, rsi_vals

    def _top_divergence(self, closes: List[float], r: float, rsi_vals) -> bool:
        if r is None or not rsi_vals:
            return False
        if is_higher_high_but_lower_indicator(closes, rsi_vals, 5):
            return True
        return False

    def _bottom_divergence(self, closes: List[float], r: float, rsi_vals) -> bool:
        if r is None or not rsi_vals:
            return False
        return is_lower_low_but_higher_indicator(closes, rsi_vals, 5)

    # ------------------------------------------------------------------
    # 单一子信号判定（返回 met: bool, note: str）
    # ------------------------------------------------------------------
    def _sig_funding_positive(self, md, threshold) -> dict:
        fr = md.get("funding_rate")
        if fr is None:
            return {"met": False, "note": "资金费率数据不可用"}
        met = fr > threshold
        return {"met": met,
                "note": f"资金费率 {fr*100:.4f}% {'≥' if met else '<'} {threshold*100:.2f}%，持续为正"
                        if met else f"资金费率 {fr*100:.4f}%，未达持续为正要求"}

    def _sig_lsr(self, md, threshold) -> dict:
        lsr = md.get("lsr")
        if lsr is None:
            return {"met": False, "note": "全局多空账户比数据不可用"}
        met = lsr > threshold
        return {"met": met,
                "note": f"全局多空账户比 {lsr:.2f} {'>' if met else '<='} {threshold}"}

    def _sig_oi_drawdown(self, md, threshold) -> dict:
        oi = md.get("oi")
        oi_peak = md.get("oi_peak")
        if oi is None or oi_peak is None or oi_peak <= 0:
            return {"met": False, "note": "持仓量 OI 或峰值数据不可用"}
        drop = (oi_peak - oi) / oi_peak * 100.0
        met = drop > threshold
        return {"met": met,
                "note": f"OI {oi:.0f}，距峰值回落 {drop:.1f}% {'≥' if met else '<'} {threshold}%"}

    def _sig_hyperliquid(self, md) -> dict:
        hl = md.get("hyperliquid") or {}
        if not hl:
            return {"met": False, "note": "Hyperliquid 链上数据不可用（参考信号）"}
        hint = hl.get("hint")
        met = hl.get("bearish", False)
        return {"met": met, "note": f"Hyperliquid（参考）：{hint or '无大额平多/开空信号'}"}

    # ---------- 轨道 A 技术面（用于 spot 逃顶预警） ----------
    def _technical_track_a(self, closes: List[float], candles: List[Dict], rsi_period: int) -> List[dict]:
        r, m_line, m_sig, m_hist, rsi_vals = self._full_indicators(closes, rsi_period)
        sigs = []
        # A1: RSI/MACD 顶背离 或 出现见顶形态
        divergence = self._top_divergence(closes, r, rsi_vals)
        pattern = has_top_pattern(closes, 12)
        a1 = divergence or pattern
        detail = "顶背离" if divergence else ("M顶/圆弧顶形态" if pattern else "未出现明显顶部形态")
        sigs.append({"name": "A-技术面（顶背离/见顶形态）", "met": a1,
                     "note": detail if a1 else detail})
        # A2: 价格处于相对高位（逼近近期区间上沿）
        hi = highest(candles, 24)
        last = closes[-1] if closes else None
        near_high = (hi and last and (hi - last) / hi < 0.03)
        sigs.append({"name": "A-价格逼近近期高点", "met": bool(near_high),
                     "note": f"现价 {last:.4f}，区间高点 {hi:.4f}" 
                             if (hi and last) else "价格数据不足"})
        # A3: RSI 高位（超买）或 MACD 高位回落
        r_high = r is not None and r > 70
        sigs.append({"name": "A-RSI 高位/MACD 高位", "met": bool(r_high),
                     "note": f"RSI = {r:.1f} (>70 超买)" if r is not None else "RSI 数据不足"})
        return sigs

    # ---------- 轨道 B 技术面（现货/Alpha 共用） ----------
    def _technical_track_b(self, closes: List[float], candles: List[Dict], rsi_period: int,
                           drawdown_pct: float, rsi_oversold: float) -> List[dict]:
        r, m_line, m_sig, m_hist, rsi_vals = self._full_indicators(closes, rsi_period)
        dd = None
        if len(closes) >= 2:
            peak = max(closes[-24:])
            dd = (peak - closes[-1]) / peak * 100.0 if peak > 0 else None
        sigs = []
        # B1: 价格回撤 + 止跌形态
        hammer = is_hammer(candles[-1]) if candles else False
        engulfing = False
        if len(candles) >= 2:
            engulfing = is_bullish_engulfing(candles[-2], candles[-1])
        bottom_pattern = hammer or engulfing
        b1 = (dd is not None and dd >= drawdown_pct) and bottom_pattern
        sigs.append({"name": "B-价格回撤+止跌形态", "met": b1,
                     "note": f"自高点回撤 {dd:.1f}%{'(≥{0}%)'.format(drawdown_pct) if dd is not None and dd>=drawdown_pct else ''}，"
                             f"{'出现锤头/看涨吞没' if bottom_pattern else '未见止跌形态'}"})
        # B2: RSI 超卖并拐头
        r_turn = False
        if r is not None and r <= rsi_oversold and len(closes) > rsi_period + 2:
            prev_r = rsi(closes[-(rsi_period + 2):-1], rsi_period)
            if prev_r is not None and r > prev_r:
                r_turn = True
        sigs.append({"name": "B-RSI 超卖并拐头", "met": bool(r_turn),
                     "note": f"RSI = {r:.1f} ({'<30 且拐头向上' if r_turn else ('超卖但未拐头' if r is not None and r<=rsi_oversold else '未超卖')})"
                             if r is not None else "RSI 数据不足"})
        # B3: 底背离（技术面补充）
        sigs.append({"name": "B-技术底背离", "met": self._bottom_divergence(closes, r, rsi_vals),
                     "note": "MACD/RSI 出现底背离" if is_lower_low_but_higher_indicator(closes, rsi_vals or [], 5) else "未出现底背离"})
        return sigs

    # ------------------------------------------------------------------
    # 仓位计算（必须基于真实 K 线）
    # ------------------------------------------------------------------
    def _position_plan(self, candles: List[Dict], direction: str, params) -> Dict[str, Any]:
        atr_v = atr(candles, int(self.get("track_a.atr_period", 14)))
        a_mult = float(self.get("position.atr_mult", 1.5))
        price = current_price(candles)
        plan = {
            "stops_calculated": False,
            "note": "该指标暂不可用，止盈止损价格无法计算",
        }
        if price is None or atr_v is None:
            return plan
        if direction == "short":
            hi = highest(candles, 24)
            if hi is None:
                return plan
            hard_stop = hi + a_mult * atr_v
            stop_distance = (hard_stop - price) / price * 100.0  # 2% 规则用
            entry_ref = price
        else:  # long
            lo = lowest(candles, 24)
            if lo is None:
                return plan
            hard_stop = lo - a_mult * atr_v
            stop_distance = (price - hard_stop) / price * 100.0
            entry_ref = price

        ma10_5m = sma(closes_list(candles), 10)
        ma10_exit = sma(closes_list(candles), 10)  # 第三阶段动态离场：最新 10 周期 MA
        plan.update({
            "stops_calculated": True,
            "direction": direction,
            "atr": atr_v,
            "entry_reference": entry_ref,
            "hard_stop": hard_stop,
            "stop_calc": (f"前高 {hi:.4f} + 1.5*ATR {a_mult*atr_v:.4f}" if direction == "short"
                          else f"近期低点 {lo:.4f} - 1.5*ATR {a_mult*atr_v:.4f}"),
            "stop_distance_pct": stop_distance,
            "tp1_breakeven": entry_ref,                 # 浮盈20%移损至成本价
            "tp2_cost_plus30": entry_ref * 1.30,        # 浮盈50%移至成本+30%
            "ma10_exit": ma10_exit,
            "note": "止盈止损价格已基于真实K线计算",
        })
        return plan

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def evaluate(self, symbol, asset_type, market_data, params=None):
        cfg = params if params is not None else self.params
        candles = self._candles(market_data)
        closes = self._closes(market_data)
        price = current_price(candles)
        result: Dict[str, Any] = {
            "symbol": symbol, "type": asset_type, "price": price,
            "status": "ok", "status_note": "",
            "track_a": None, "track_b": None,
        }

        if not candles:
            result["status"] = "fetch_failed"
            result["status_note"] = "K线数据为空，无法判定"
            return result

        ta_p = cfg.get("track_a", {})
        tb_p = cfg.get("track_b", {})
        rsi_period = int(ta_p.get("rsi_period", 14))

        # ---------------- 轨道 A ----------------
        if asset_type == "futures":
            sigs_a = [
                self._sig_funding_positive(market_data, float(ta_p.get("funding_rate_threshold", 0.001))),
                self._sig_lsr(market_data, float(ta_p.get("lsr_threshold", 1.5))),
                self._technical_top_signal(candles, closes, rsi_period),
                self._sig_oi_drawdown(market_data, float(ta_p.get("oi_drawdown_pct", 15.0))),
                self._sig_hyperliquid(market_data),  # 参考
            ]
            # 核心 = 前 4 项，参考 = 第 5 项
            core_a = sigs_a[:4]
            ref_a = sigs_a[4]
            core_met = sum(1 for s in core_a if s["met"])
            min_warn = int(ta_p.get("min_core_for_warning", 3))
            min_trig = int(ta_p.get("min_core_for_trigger", 4))
            warning = core_met >= min_warn
            trigger = core_met >= min_trig
            track_a = {
                "available": True, "count": core_met, "total": len(core_a),
                "warning": warning, "triggered": trigger,
                "action": "见顶做空" if trigger else ("预警" if warning else "观察"),
                "core": core_a, "reference": ref_a,
            }
            if trigger:
                track_a["position"] = self._position_plan(candles, "short", cfg)
            result["track_a"] = track_a
        elif asset_type == "spot":
            # 仅逃顶预警（建议减仓），不考虑做空
            sigs_a = self._technical_track_a(closes, candles, rsi_period)
            core_met = sum(1 for s in sigs_a if s["met"])
            warning = core_met >= 2
            result["track_a"] = {
                "available": True, "count": core_met, "total": len(sigs_a),
                "warning": warning, "triggered": False,
                "action": "逃顶预警（建议减仓）" if warning else "观察",
                "core": sigs_a, "reference": None,
            }
        else:
            # alpha：无轨道A
            result["track_a"] = None

        # ---------------- 轨道 B ----------------
        if asset_type == "futures":
            r, m_line, m_sig, m_hist, rsi_vals = self._full_indicators(closes, rsi_period)
            # 核心 3 条件：(1) 技术面 (2) OI 企稳回升 (3) 资金费率转负
            c1 = self._sig_technical_bottom(
                closes, candles, rsi_period,
                float(tb_p.get("drawdown_pct", 15.0)),
                float(tb_p.get("rsi_oversold", 30)),
            )
            c2 = self._sig_oi_stabilize(market_data, tb_p)
            c3 = self._sig_funding_negative(market_data, tb_p)
            core_b = [c1, c2, c3]
            # 详细技术子信号，仅用于邮件展示
            details_b = self._technical_track_b(
                closes, candles, rsi_period,
                float(tb_p.get("drawdown_pct", 15.0)),
                float(tb_p.get("rsi_oversold", 30)),
            )
            core_met = sum(1 for s in core_b if s["met"])
            min_warn = int(tb_p.get("min_core_for_warning", 2))
            min_trig = int(tb_p.get("min_core_for_trigger", 3))
            warning = core_met >= min_warn
            trigger = core_met >= min_trig
            track_b = {
                "available": True, "count": core_met, "total": len(core_b),
                "warning": warning, "triggered": trigger,
                "action": "暴跌抄底（做多）" if trigger else ("预警" if warning else "观察"),
                "core": core_b, "leva": details_b,
            }
            if trigger:
                track_b["position"] = self._position_plan(candles, "long", cfg)
            result["track_b"] = track_b
        elif asset_type in ("spot", "alpha"):
            sigs_b = self._technical_track_b(closes, candles, rsi_period,
                                             float(tb_p.get("drawdown_pct", 15.0)),
                                             float(tb_p.get("rsi_oversold", 30)))
            core_met = sum(1 for s in sigs_b if s["met"])
            min_warn = int(tb_p.get("min_core_for_warning", 2))
            min_trig = int(tb_p.get("min_core_for_trigger", 3))
            warning = core_met >= min_warn
            trigger = core_met >= min_trig
            track_b = {
                "available": True, "count": core_met, "total": len(sigs_b),
                "warning": warning, "triggered": trigger,
                "action": "暴跌抄底（做多）" if trigger else ("预警" if warning else "观察"),
                "core": sigs_b, "leva": [],
            }
            if trigger:
                track_b["position"] = self._position_plan(candles, "long", cfg)
            result["track_b"] = track_b

        return result

    # ---- futures 轨道 A 技术子信号（顶背离/形态） ----
    def _technical_top_signal(self, candles, closes: List[float], rsi_period) -> dict:
        r, m_line, m_sig, m_hist, rsi_vals = self._full_indicators(closes, rsi_period)
        divergence = self._top_divergence(closes, r, rsi_vals)
        pattern = has_top_pattern(closes, 12)
        met = divergence or pattern
        why = "顶背离" if divergence else ("M顶/圆弧顶形态" if pattern else "未见顶背离/见顶形态")
        return {"name": "A-技术面（顶背离/M顶）", "met": met, "note": why}

    def _sig_oi_stabilize(self, md, tb_p) -> dict:
        oi = md.get("oi")
        oi_low = md.get("oi_low")
        oi_peak = md.get("oi_peak")
        if oi is None or oi_low is None or oi_peak is None or oi_peak <= 0:
            return {"met": False, "note": "OI 企稳数据不可用"}
        dropped_much = (oi_peak - oi_low) / oi_peak > 0.10
        rebound = oi > oi_low
        active = md.get("trade_long_ratio")
        active_pos = False
        if active is not None:
            active_pos = active > 0.5  # 主动买卖比由负转正（>0.5 表示买盘占优）
        met = dropped_much and (rebound or active_pos)
        return {"met": met,
                "note": f"OI 大幅下降后企稳回升={rebound}，主动买卖比转正={active_pos}"}

    def _sig_funding_negative(self, md, tb_p) -> dict:
        fr = md.get("funding_rate")
        if fr is None:
            return {"met": False, "note": "资金费率数据不可用"}
        met = fr < 0
        return {"met": met, "note": f"资金费率 {fr*100:.4f}% {'已转负' if met else '未转负'}"}

    def _sig_technical_bottom(self, closes: List[float], candles: List[Dict],
                              rsi_period: int, drawdown_pct: float,
                              rsi_oversold: float) -> dict:
        """
        轨道 B 核心条件(1)：价格回撤≥阈值 + 出现止跌形态 + RSI<阈值并拐头。
        三项同时满足才算达成该核心条件。
        """
        r, m_line, m_sig, m_hist, rsi_vals = self._full_indicators(closes, rsi_period)
        dd = None
        if len(closes) >= 2:
            peak = max(closes[-24:])
            dd = (peak - closes[-1]) / peak * 100.0 if peak > 0 else None
        hammer = is_hammer(candles[-1]) if candles else False
        engulfing = False
        if len(candles) >= 2:
            engulfing = is_bullish_engulfing(candles[-2], candles[-1])
        bottom_pattern = hammer or engulfing
        r_turn = False
        if r is not None and r <= rsi_oversold and len(closes) > rsi_period + 2:
            prev_r = rsi(closes[-(rsi_period + 2):-1], rsi_period)
            if prev_r is not None and r > prev_r:
                r_turn = True
        parts = []
        parts.append(f"回撤{dd:.1f}%{'≥' if dd is not None and dd >= drawdown_pct else '<'}{drawdown_pct}%"
                     if dd is not None else "回撤不可用")
        parts.append("有止跌形态" if bottom_pattern else "无止跌形态")
        parts.append(f"RSI={r:.1f} 超卖拐头" if r_turn else ("RSI超卖未拐头" if r is not None and r <= rsi_oversold else "RSI未超卖"))
        met = (dd is not None and dd >= drawdown_pct) and bottom_pattern and r_turn
        return {"name": "B-技术面（回撤+止跌+RSI拐头）", "met": met, "note": "；".join(parts)}