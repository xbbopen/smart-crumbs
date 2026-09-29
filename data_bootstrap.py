"""
首次历史数据拉取脚本。
核心：使用 fetch_klines_from_now 从最新往前分段拉取，瀑布式降级。
"""
import argparse
import json
import time
import logging
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from data_db import get_last_timestamp, upsert_klines, count_klines, db_size_mb, source_stats
from data_fetcher import (
    fetch_hyperliquid_klines, fetch_binance_spot_klines, fetch_gateio_spot_klines,
    fetch_klines_from_now,
    to_hyperliquid_coin, get_hyperliquid_universe,
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

BJT = timezone(timedelta(hours=8))

TARGET_BARS = {
    "30m": 500,
    "4h": 2000,
}


def resolve_source(symbol, asset_type):
    if asset_type == "futures":
        coin = to_hyperliquid_coin(symbol)
        if coin in get_hyperliquid_universe():
            return "hyperliquid", fetch_hyperliquid_klines
    return "binance_spot", fetch_binance_spot_klines


def fetch_with_cascade(symbol, interval, primary_source, primary_fetch, target):
    """
    瀑布式降级拉取：
    1. 主源
    2. 主源失败 → 币安现货
    3. 币安失败 → Gate现货
    返回 (klines, actual_source)
    """
    # 第一层：主源
    if primary_source == "hyperliquid":
        log.info(f"  [{interval}] 尝试主源 Hyperliquid")
        klines = fetch_klines_from_now(primary_fetch, symbol, interval, target)
        if klines:
            return klines, "hyperliquid"
        log.warning(f"  [{interval}] Hyperliquid 失败，降级币安现货")

    # 第二层：币安现货
    log.info(f"  [{interval}] 尝试币安现货")
    klines = fetch_klines_from_now(fetch_binance_spot_klines, symbol, interval, target)
    if klines:
        return klines, "binance_spot"

    # 第三层：Gate现货
    log.warning(f"  [{interval}] 币安失败，降级Gate现货")
    klines = fetch_klines_from_now(fetch_gateio_spot_klines, symbol, interval, target)
    if klines:
        return klines, "gate_spot"

    # 全部失败
    log.error(f"  [{interval}] 所有源都失败")
    return None, None


def bootstrap_one(symbol, asset_type, intervals):
    log.info(f"\n{'='*60}")
    log.info(f"处理 {symbol} ({asset_type})")
    log.info(f"{'='*60}")

    primary_source, primary_fetch = resolve_source(symbol, asset_type)
    log.info(f"  主数据源: {primary_source}")

    for interval in intervals:
        target = TARGET_BARS.get(interval, 500)

        # 检查是否已有数据（只要有数据就跳过，不管来自哪个源）
        last_ts = get_last_timestamp(symbol, interval, source=None)
        if last_ts:
            total = count_klines(symbol, interval)
            log.info(f"  [{interval}] 已有数据（{total}根），跳过")
            continue

        # 瀑布式拉取
        klines, actual_source = fetch_with_cascade(symbol, interval, primary_source, primary_fetch, target)

        if klines:
            inserted = upsert_klines(symbol, interval, klines, source=actual_source)
            total = count_klines(symbol, interval)
            stats = source_stats(symbol, interval)
            log.info(f"  [{interval}] ✅ 写入 {inserted} 根（source={actual_source}），累计 {total} 根，分布: {stats}")
        else:
            log.warning(f"  [{interval}] ❌ 所有源都失败，跳过")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--from-config", action="store_true")
    parser.add_argument("--intervals", nargs="*", default=["30m", "4h"])
    args = parser.parse_args()
    
    # 🚀 一次性压缩数据库（使用统一封装）
    from data_db import vacuum_db
    if os.path.exists("data/market.db"):
        log.info("正在压缩数据库（VACUUM）...")
        before, after = vacuum_db()
        log.info(f"✅ 压缩完成：{before:.2f} MB → {after:.2f} MB")
        
    if args.from_config:
        with open("config/config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        watchlist = cfg.get("watchlist", [])
    elif args.symbols:
        watchlist = [{"symbol": s, "type": "futures"} for s in args.symbols]
    else:
        log.error("必须指定 --symbols 或 --from-config")
        return

    log.info(f"📊 Bootstrap 开始：{len(watchlist)} 个标的")
    log.info(f"   目标根数: 30m=500，4h=2000")
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
