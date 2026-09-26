"""
使用 Crawl4AI 从币安官网抓取合约页面的实时价格和资金费率
依赖：pip install crawl4ai playwright playwright-stealth
"""
import asyncio
import json
import logging
import re
from urllib.parse import quote

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
from playwright_stealth import stealth

log = logging.getLogger(__name__)

async def fetch_binance_futures_page(symbol: str):
    """抓取币安合约页面数据"""
    url = f"https://www.binance.com/zh-CN/futures/{symbol}"
    browser_config = BrowserConfig(
        browser_type="chromium", headless=True,
        viewport_width=1920, viewport_height=1080,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # 等待页面元素加载
    wait_for = """() => {
        return document.querySelector('[data-testid="price"]') !== null
            || document.querySelector('[class*="price"]') !== null
            || document.querySelector('[class*="funding"]') !== null;
    }"""
    # 定义提取规则（首次运行若失败，请查看markdown调整selector）
    extraction_schema = {
        "name": "Binance Futures Data",
        "baseSelector": "body",
        "fields": [
            {"name": "current_price", "selector": "[data-testid='price']", "type": "text"},
            {"name": "funding_rate", "selector": "[class*='fundingRate']", "type": "text"},
        ]
    }
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS, wait_for=wait_for, page_timeout=30000,
        extraction_strategy=JsonCssExtractionStrategy(extraction_schema, verbose=False),
        js_code=["window.scrollTo(0, document.body.scrollHeight/2);"],
    )
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
            if not result.success:
                log.error(f"爬取失败 [{symbol}]: {result.error_message}")
                return None
            
            parsed = {}
            if result.extracted_content:
                try:
                    data = json.loads(result.extracted_content)
                    if data and isinstance(data, list):
                        first = data[0]
                        price_str = str(first.get("current_price", "")).replace(",", "").replace("$", "")
                        if price_str: parsed["current_price"] = float(price_str)
                        funding_str = str(first.get("funding_rate", "")).replace("%", "").replace(",", "").replace("+", "")
                        if funding_str: parsed["funding_rate"] = float(funding_str)
                except Exception: pass
            
            # 降级正则提取
            if result.markdown:
                if "current_price" not in parsed:
                    m = re.search(r'\$?([\d,]+\.?\d*)', result.markdown[:3000])
                    if m: parsed["current_price"] = float(m.group(1).replace(",", ""))
                if "funding_rate" not in parsed:
                    m = re.search(r'([+-]?\d+\.?\d*)\s*%', result.markdown[:5000])
                    if m: parsed["funding_rate"] = float(m.group(1))
            
            log.info(f"[{symbol}] 爬虫抓取成功: {parsed}")
            return parsed
    except Exception as e:
        log.error(f"爬虫异常 [{symbol}]: {e}")
        return None

def fetch_price_and_funding_via_crawler(symbol: str):
    """同步调用接口"""
    raw = symbol.replace("_", "").upper()
    encoded = quote(raw, safe="")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # 注入 stealth 规避反爬
        stealth(loop)
        result = loop.run_until_complete(fetch_binance_futures_page(encoded))
        loop.close()
        return result
    except Exception as e:
        log.error(f"爬虫同步调用失败: {e}")
        return None
