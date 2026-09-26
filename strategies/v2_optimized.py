# -*- coding: utf-8 -*-
"""
v2_optimized：预留的优化策略空壳。

当前不参与正式信号判定，只作为"影子策略"被计算并附在邮件末尾观察。
新策略开发时，重写 evaluate() 即可，无需改动 monitor.py。
"""
from __future__ import annotations

from typing import Any, Dict

from .base import BaseStrategy


class V2OptimizedStrategy(BaseStrategy):
    name = "v2_optimized"

    def evaluate(self, symbol, asset_type, market_data, params=None):
        # 空壳策略：始终返回"无信号"占位结果，供影子模式观察。
        return {
            "symbol": symbol,
            "type": asset_type,
            "price": None,
            "status": "ok",
            "status_note": "v2_optimized 为预留策略，仅返回占位结果，不参与判定",
            "track_a": None,
            "track_b": None,
            "shadow_placeholder": True,
        }