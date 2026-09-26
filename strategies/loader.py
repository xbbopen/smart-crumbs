# -*- coding: utf-8 -*-
"""
loader：根据策略名从 config/strategies/registry.json 动态导入并返回策略实例。

registry.json 形如：
{
  "v1_default": {"module": "v1_default", "class": "V1DefaultStrategy"},
  "v2_optimized": {"module": "v2_optimized", "class": "V2OptimizedStrategy"}
}
"""
from __future__ import annotations

import json
import importlib
from typing import Any, Dict, Optional
from pathlib import Path

from .base import BaseStrategy

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "strategies"


def _load_registry() -> Dict[str, Dict[str, str]]:
    reg_path = _CONFIG_DIR / "registry.json"
    if not reg_path.exists():
        raise FileNotFoundError(f"策略注册表不存在: {reg_path}")
    with open(reg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_strategy(name: str,
                  params: Optional[Dict[str, Any]] = None) -> BaseStrategy:
    """按策略名加载策略实例；参数 params 若为空则从 v1_default.json 同名文件读取。"""
    registry = _load_registry()
    if name not in registry:
        raise ValueError(f"策略 '{name}' 未在 registry.json 中注册，可用策略: {list(registry.keys())}")

    meta = registry[name]
    module = importlib.import_module(f"strategies.{meta['module']}")
    cls = getattr(module, meta["class"])

    # 参数默认从 config/strategies/{name}.json 读取
    p = params
    if not p:
        param_path = _CONFIG_DIR / f"{name}.json"
        if param_path.exists():
            with open(param_path, "r", encoding="utf-8") as f:
                p = json.load(f)
    return cls(p)


def list_available() -> list:
    return list(_load_registry().keys())