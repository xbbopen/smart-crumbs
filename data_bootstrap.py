"""
首次历史数据拉取脚本。
v3：统一使用 data_fetcher.fetch_and_cache_klines，与 monitor / backtest 保持完全一致。
"""
import argparse
import json
import time
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from data_db import count_klines, db_size_mb, source_stats, vacuum_db
from data_fetcher import fetch_and_cache_klines

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

BJT = timezone(timedelta(hours=8))

# 建库目标根数（与回测对齐）
TARGET_BARS = {
    "30m": 5000,
    "4h": 3000,
    "1d": 500,
}


def _load_backtest_symbols(cfg):
    """从 config.json 读取建库标的。优先 backtest_tiers，降级 backtest_watchlist，再降级 watchlist。"""
    tiers = cfg.get("backtest_tiers", {})
    if tiers:
        symbols = []
        seen = set()
        for tier_name in ["core", "satellite", "watch"]:
            for sym in tiers.get(tier_name, []):
                if sym not in seen:
                    seen.add(sym)
                    symbols.append({"symbol": sym, "type": "futures"})
        if symbols:
            log.info(f"📋 从 backtest_tiers 读取到 {len(symbols)} 个标的")
            return symbols

    bt_list = cfg.get("backtest_watchlist", [])
    if bt_list:
        log.info(f"📋 从 backtest_watchlist 读取到 {len(bt_list)} 个标的")
        return [{"symbol": s, "type": "futures"} for s in bt_list]

    watchlist = cfg.get("watchlist", [])
    log.info(f"📋 降级从 watchlist 读取到 {len(watchlist)} 个标的")
    return watchlist


def bootstrap_one(symbol, asset_type, intervals):
    """单个标的：循环调用 fetch_and_cache_klines 拉取各周期。"""
    log.info(f"\n{'='*60}")
    log.info(f"处理 {symbol} ({asset_type})")
    log.info(f"{'='*60}")

    for interval in intervals:
        target = TARGET_BARS.get(interval, 500)
        try:
            klines, info = fetch_and_cache_klines(symbol, asset_type, interval, target)
            if klines:
                total = count_klines(symbol, interval)
                stats = source_stats(symbol, interval)
                log.info(f"  [{interval}] ✅ 就绪 {len(klines)} 根（source={info.get('actual','?')}），"
                         f"累计 {total} 根，分布: {stats}")
            else:
                log.warning(f"  [{interval}] ❌ 所有源都失败，跳过")
        except Exception as e:
            log.error(f"  [{interval}] ❌ 异常: {type(e).__name__}: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--from-config", action="store_true")
    parser.add_argument("--intervals", nargs="*", default=["30m", "4h", "1d"])
    args = parser.parse_args()

    # VACUUM
    if os.path.exists("data/market.db"):
        log.info("正在压缩数据库（VACUUM）...")
        before, after = vacuum_db()
        log.info(f"✅ 压缩完成：{before:.2f} MB → {after:.2f} MB")

    if args.from_config:
        with open("config/config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        watchlist = _load_backtest_symbols(cfg)
    elif args.symbols:
        watchlist = [{"symbol": s, "type": "futures"} for s in args.symbols]
    else:
        log.error("必须指定 --symbols 或 --from-config")
        return

    log.info(f"📊 Bootstrap 开始：{len(watchlist)} 个标的")
    log.info(f"   目标根数: {TARGET_BARS}")
    log.info(f"   当前数据库大小：{db_size_mb():.2f} MB")

    start_time = time.time()
    for idx, item in enumerate(watchlist, 1):
        sym = item["symbol"]
        typ = item.get("type", "futures")
        log.info(f"\n[{idx}/{len(watchlist)}]")
        try:
            bootstrap_one(sym, typ, args.intervals)
        except Exception as e:
            log.error(f"  ❌ {sym} 处理异常: {e}")
            continue

    elapsed = time.time() - start_time
    log.info(f"\n{'='*60}")
    log.info(f"✅ Bootstrap 完成，用时 {elapsed:.1f} 秒")
    log.info(f"   数据库大小：{db_size_mb():.2f} MB")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
