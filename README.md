# 多标的合约三轨监控系统

基于 GitHub Actions 的全自动合约行情监控系统。数据源采用 **Hyperliquid** 公开 API，无需 API Key，无 IP 地理封锁。

## 🎯 核心目标

捕捉合约市场中三种极端机会：
1. **底部震荡后的稳定上行**（轨道1：做多）
2. **见顶后的做空机会**（轨道2：做空）
3. **见顶后暴跌的强势反弹**（轨道3：做多）

## 🏗️ 项目架构
crypto-monitor/
├── .github/workflows/monitor.yml
├── config/config.json
├── strategies/
│ ├── base.py
│ ├── v1_default.py
│ └── loader.py
├── monitor.py
├── email_sender.py
├── requirements.txt
└── README.md

## 📊 三轨策略逻辑（基于30分钟K线，150根历史）

### 🟩 轨道1：底部震荡突破（做多）
- **信号**：①价格≤近期低点×1.05；②站上MA10；③RSI在45-65；④成交量放大1.2倍。满足4个中的3个触发。

### 🟥 轨道2：见顶做空
- **信号**：①价格≥近期高点×0.97；②RSI≥70；③资金费率>0.05%；④连续3根阴线。满足4个中的3个触发。

### 🟩 轨道3：暴跌后强势反弹（做多）
- **信号**：①从高点回撤≥15%；②RSI≤35；③站上MA10或出现长下影线。满足3个中的3个触发。

## 🔑 币种符号映射（重要）

Hyperliquid API 要求 `coin` 参数使用**基础货币符号**（如 `ZEC`、`BTC`），而非交易对格式（如 `ZEC_USDT`）。

`monitor.py` 中的 `symbol_to_coin()` 函数会自动完成转换：
- `ZEC_USDT` → `ZEC`
- `BTC_USDT` → `BTC`
- `NEAR_USDT` → `NEAR`

## 📐 《以交易为生》交易计划

触发信号后，邮件自动附带：硬止损价、2%资金管理仓位建议、移动止盈触发价（20%/50%）、MA10动态离场线。

## ⚙️ 部署指南（GitHub Actions）

1. 将本项目上传到 GitHub 仓库。
2. 配置 Gmail 应用密码，并在仓库 Secrets 中添加：
   - `SMTP_SERVER`: `smtp.gmail.com`
   - `SMTP_PORT`: `587`
   - `SMTP_USERNAME`: 你的Gmail
   - `SMTP_PASSWORD`: 16位应用密码
   - `RECIPIENT_EMAIL`: 接收邮箱
3. 进入 Actions 页面，手动触发 `Run workflow` 测试。

## 🛠️ 如何添加或删除监控标的？

修改 `config/config.json` 中的 `watchlist` 数组：
```json
"watchlist": [
  { "symbol": "BTC_USDT", "type": "futures" },
  { "symbol": "ZEC_USDT", "type": "futures" }
]
