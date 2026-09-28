"""
首次历史数据拉取脚本。
用法：
    python data_bootstrap.py --from-config
    python data_bootstrap.py --symbols BTC ETH SOL --intervals 30m 4h --days 365
支持断点续传（DB里已有数据会跳过）。
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
    to_hyperliquid_coin, get_hyperliquid_universe,
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

BJT = timezone(timedelta(hours=8))
INTERVAL_MS = {"30m": 30 * 60 * 1000, "4h": 4 * 60 * 60 * 1000}


def fetch_klines_paged(fetch_func, symbol, interval, start_ms, end_ms, page_interval_ms):
    """分页拉取，单页上限5000根"""
    all_klines = []
    cur = start_ms
    for _ in range(20):  # 最多20次分页
        if cur >= end_ms:
            break
        chunk_end = min(cur + page_interval_ms * 5000, end_ms)
        klines, status = fetch_func(symbol, interval, 5000, cur, chunk_end)
        if not klines:
            log.warning(f"    [{symbol}][{interval}] 分段拉取失败: {status}")
            break
        all_klines.extend(klines)
        last_ts = klines[-1]["timestamp"]
        if last_ts <= cur:
            break
        cur = last_ts + page_interval_ms
    return all_klines


def resolve_source(symbol, asset_type):
    """
    决定该标的的主源。返回 (source_name, fetch_func)
    规则：
    - futures + Hyperliquid有合约 → hyperliquid
    - 其他所有情况 → binance_spot
    """
    if asset_type == "futures":
        coin = to_hyperliquid_coin(symbol)
        if coin in get_hyperliquid_universe():
            return "hyperliquid", fetch_hyperliquid_klines
    return "binance_spot", fetch_binance_spot_klines


def bootstrap_one(symbol, asset_type, intervals, days_back):
    log.info(f"\n{'='*60}")
    log.info(f"处理 {symbol} ({asset_type})")
    log.info(f"{'='*60}")

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days_back * 24 * 60 * 60 * 1000)

    primary_source, primary_fetch = resolve_source(symbol, asset_type)
    log.info(f"  主数据源: {primary_source}")

    for interval in intervals:
        page_ms = INTERVAL_MS.get(interval, 30 * 60 * 1000)
        last_ts = get_last_timestamp(symbol, interval, source=primary_source)

        if last_ts:
            fetch_start = last_ts + page_ms
            log.info(f"  [{interval}] 主源已有数据，从该点续传")
        else:
            fetch_start = start_ms
            start_dt = datetime.fromtimestamp(start_ms / 1000, BJT)
            log.info(f"  [{interval}] 从 {start_dt} 开始全量拉取")

        if fetch_start >= now_ms:
            log.info(f"  [{interval}] 数据已是最新，跳过")
            continue

        klines = fetch_klines_paged(primary_fetch, symbol, interval, fetch_start, now_ms, page_ms)
        actual_source = primary_source

        # 主源失败降级
        if not klines and primary_source == "hyperliquid":
            log.warning(f"  [{interval}] Hyperliquid 失败，降级币安现货")
            klines = fetch_klines_paged(fetch_binance_spot_klines, symbol, interval, fetch_start, now_ms, page_ms)
            actual_source = "binance_spot"
            if not klines:
                log.warning(f"  [{interval}] 币安失败，降级Gate现货")
                klines = fetch_klines_paged(fetch_gateio_spot_klines, symbol, interval, fetch_start, now_ms, page_ms)
                actual_source = "gate_spot"

        if klines:
            inserted = upsert_klines(symbol, interval, klines, source=actual_source)
            total = count_klines(symbol, interval)
            stats = source_stats(symbol, interval)
            log.info(f"  [{interval}] ✅ 写入 {inserted} 根（source={actual_source}），累计 {total} 根，分布: {stats}")
        else:
            log.warning(f"  [{interval}] ❌ 未拉取到数据")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--from-config", action="store_true")
    parser.add_argument("--intervals", nargs="*", default=["30m", "4h"])
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    if args.from_config:
        with open("config/config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        watchlist = cfg.get("watchlist", [])
    elif args.symbols:
        watchlist = [{"symbol": s, "type": "futures"} for s in args.symbols]
    else:
        log.error("必须指定 --symbols 或 --from-config")
        return

    log.info(f"📊 Bootstrap 开始：{len(watchlist)} 个标的，周期 {args.intervals}，回溯 {args.days} 天")
    log.info(f"   当前数据库大小：{db_size_mb():.2f} MB")

    start_time = time.time()
    for idx, item in enumerate(watchlist, 1):
        sym = item["symbol"]
        typ = item.get("type", "futures")
        log.info(f"\n[{idx}/{len(watchlist)}]")
        try:
            bootstrap_one(sym, typ, args.intervals, args.days)
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
