# -*- coding: utf-8 -*-
"""
BaseStrategy 接口与通用技术指标工具。
monitor.py 负责拉取原始市场数据并按固定结构组装成 market_data，
策略子类只需要基于传入的 market_data + params 完成信号判定与仓位计算。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 通用技术指标（纯函数，不依赖第三方计算库）
# ---------------------------------------------------------------------------

def sma(values: List[float], period: int) -> Optional[float]:
    """简单移动平均，返回最近一个周期的均值；数据不足返回 None。"""
    if not values or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: List[float], period: int) -> List[float]:
    """指数移动平均，返回与输入等长的数组。"""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    """Wilder 平滑 RSI。数据不足返回 None。"""
    n = len(values)
    if n < period + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """
    返回 (macd_line, signal_line, hist)，均为与输入等长的列表。
    数据不足 slow+signal 时返回 (None, None, None)。
    """
    if len(values) < slow + signal:
        return None, None, None
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    sig = ema(macd_line, signal)
    sig = sig[-(len(macd_line)):] if len(sig) > len(macd_line) else sig
    hist = [m - s for m, s in zip(macd_line, sig)]
    return macd_line, sig, hist


def atr(candles: List[Dict[str, float]], period: int = 14) -> Optional[float]:
    """Wilder 平均真实波幅。根据 K 线 dict(h/l/c) 计算。"""
    n = len(candles)
    if n < period + 1:
        return None
    trs: List[float] = []
    prev_close = candles[0]["c"]
    for i in range(1, n):
        c = candles[i]
        tr = max(c["h"] - c["l"],
                 abs(c["h"] - prev_close),
                 abs(c["l"] - prev_close))
        trs.append(tr)
        prev_close = c["c"]
    atr_val = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
    return atr_val


def max_drawdown_pct(closes: List[float], lookback: int) -> Optional[float]:
    """最近 lookback 根 K 线内，距区间最高价的回撤百分比（正数表示回撤幅度）。"""
    if len(closes) < 2:
        return None
    window = closes[-lookback:]
    peak = max(window)
    if peak <= 0:
        return None
    return (peak - window[-1]) / peak * 100.0


def is_higher_high_but_lower_indicator(price: List[float], ind: List[float], look: int = 5) -> bool:
    """
    顶背离：价格创新高但指标(如 RSI)反而走低。
    取最近两段"波段"的高点近似判断，简单可靠。
    """
    if len(price) < look + 2 or len(ind) < look + 2:
        return False
    p1, p2 = price[-look:], price[-(look + 2):-2]
    i1, i2 = ind[-look:], ind[-(look + 2):-2]
    if not p1 or not p2 or not i1 or not i2:
        return False
    price_higher = max(p1) > max(p2)
    ind_lower = max(i1) < max(i2)
    return price_higher and ind_lower


def is_lower_low_but_higher_indicator(price: List[float], ind: List[float], look: int = 5) -> bool:
    """底背离：价格创新低但指标走高。"""
    if len(price) < look + 2 or len(ind) < look + 2:
        return False
    p1, p2 = price[-look:], price[-(look + 2):-2]
    i1, i2 = ind[-look:], ind[-(look + 2):-2]
    if not p1 or not p2 or not i1 or not i2:
        return False
    price_lower = min(p1) < min(p2)
    ind_higher = min(i1) > min(i2)
    return price_lower and ind_higher


# ---------------------------------------------------------------------------
# K 线形态判断（基于最后一两根 K 线，返回布尔）
# ---------------------------------------------------------------------------

def _candle_body(o: float, c: float) -> float:
    return abs(c - o)


def is_hammer(c: Dict[str, float]) -> bool:
    """锤头线：下影线明显长于实体，上影线很短。"""
    high, low, o, close = c["h"], c["l"], c["o"], c["c"]
    body = _candle_body(o, close)
    rng = high - low
    if rng <= 0:
        return False
    lower_shadow = min(o, close) - low
    upper_shadow = high - max(o, close)
    return body > 0 and lower_shadow >= 2.0 * body and upper_shadow <= body


def is_bullish_engulfing(c1: Dict[str, float], c2: Dict[str, float]) -> bool:
    """看涨吞没：后一根阳线完整吞没前一根阴线。"""
    return (c1["c"] < c1["o"] and c2["c"] > c2["o"]
            and c2["o"] <= c1["c"] and c2["c"] >= c1["o"])


def is_bearish_engulfing(c1: Dict[str, float], c2: Dict[str, float]) -> bool:
    """看跌吞没：后一根阴线完整吞没前一根阳线。"""
    return (c1["c"] > c1["o"] and c2["c"] < c2["o"]
            and c2["o"] >= c1["c"] and c2["c"] <= c1["o"])


def has_top_pattern(closes: List[float], look: int = 12) -> bool:
    """
    粗略判断 M 顶 / 圆弧顶：近期收盘价已从区间高点回落一截，
    且高于区间低点（尚未破位），属于见顶形态雏形。
    """
    if len(closes) < look:
        return False
    window = closes[-look:]
    peak = max(window)
    trough = min(window)
    last = window[-1]
    if peak <= 0:
        return False
    drawdown = (peak - last) / peak
    # 从高点回落 2%~10% 且仍高于区间低点上方 5%
    return 0.02 <= drawdown <= 0.10 and (last - trough) / peak >= 0.03


def has_bottom_pattern(closes: List[float], look: int = 12) -> bool:
    """粗略判断止跌形态：近期已探低后小幅回升（出现锤头/看涨吞没由调用方再细化）。"""
    if len(closes) < look:
        return False
    window = closes[-look:]
    trough = min(window)
    last = window[-1]
    if trough <= 0:
        return False
    return (last - trough) / trough >= 0.005 and last >= trough


# ---------------------------------------------------------------------------
# 数据归一化
# ---------------------------------------------------------------------------

def normalize_candles(raw: List[Any], source: str = "gate") -> List[Dict[str, float]]:
    """
    把不同数据源返回的 K 线数组统一成 dict 列表 [{t,o,h,l,c,v}]。

    各数据源数组字段顺序（已按官方文档核实）：
    - Gate.io REST/CSV（futures 与 spot 一致）: [timestamp, volume, close, highest, lowest, open]
    - Binance: [openTime, open, high, low, close, volume, ...]
    传入 source 为 'binance' 时按 Binance 顺序解析；否则按 Gate 顺序解析。
    """
    out: List[Dict[str, float]] = []
    for item in raw:
        try:
            if not item:
                continue
            if len(item) == 1 and isinstance(item[0], (list, tuple)):
                item = item[0]
            item = list(item)
            if len(item) < 5:
                continue
            try:
                if source == "binance":
                    o = float(item[1]); h = float(item[2]); l = float(item[3]); c = float(item[4])
                    v = float(item[5]) if len(item) > 5 else 0.0
                else:  # gate: [t, v, c, h, l, o]
                    v = float(item[1]); c = float(item[2]); h = float(item[3]); l = float(item[4])
                    o = float(item[5]) if len(item) > 5 else c
            except (TypeError, ValueError, IndexError):
                continue
            out.append({"t": float(item[0]), "o": o, "h": h, "l": l, "c": c, "v": v})
        except (TypeError, ValueError, IndexError):
            continue
    # 按时间升序排序，确保最新一根在末尾
    out.sort(key=lambda x: x["t"])
    return out


def current_price(candles: List[Dict[str, float]]) -> Optional[float]:
    """取最后一根 K 线的收盘价作为当前价。"""
    if not candles:
        return None
    return candles[-1]["c"]


def closes_list(candles: List[Dict[str, float]]) -> List[float]:
    return [c["c"] for c in candles]


def highest(candles: List[Dict[str, float]], look: int) -> Optional[float]:
    if not candles:
        return None
    return max(c["h"] for c in candles[-look:])


def lowest(candles: List[Dict[str, float]], look: int) -> Optional[float]:
    if not candles:
        return None
    return min(c["l"] for c in candles[-look:])


# ---------------------------------------------------------------------------
# 策略基类
# ---------------------------------------------------------------------------

class BaseStrategy:
    """策略基类。子类实现 evaluate()。"""

    name = "base"

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = params or {}

    def get(self, path: str, default: Any = None) -> Any:
        """从嵌套 params 取参数，如 track_a.funding_rate_threshold。"""
        node: Any = self.params
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def evaluate(self, symbol: str, asset_type: str,
                 market_data: Dict[str, Any],
                 params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        返回统一结构的结果 dict。子类必须实现。

        market_data 结构（由 monitor.py 组装）：
        {
          "candles": [...],           # normalize_candles 结果
          "funding_rate": float|None,
          "oi": float|None,
          "lsr": float|None,          # 全局多空账户比
          "trade_long_ratio": float|None,  # 主动买卖比
          "hyperliquid": dict|None,   # 参考信号
          "asset_type": str,
        }
        """
        raise NotImplementedError