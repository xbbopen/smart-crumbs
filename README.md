# 多标的合约三轨监控系统

基于 GitHub Actions 的全自动合约行情监控系统。数据源采用 **Hyperliquid（主）+ Gate.io（备用降级）** 双轨策略，最大化币种覆盖。

## 🎯 核心目标
捕捉三种极端机会：
1. **底部震荡后的稳定上行**（轨道1：做多）
2. **见顶后的做空机会**（轨道2：做空）
3. **见顶后暴跌的强势反弹**（轨道3：做多）

## 📊 三轨策略逻辑（基于30分钟K线，300根历史）

### 🟩 轨道1：底部震荡突破（做多）
- **信号**：①价格≤近期低点×1.05；②站上MA10；③RSI在45-65；④成交量放大1.2倍。满足4个中的3个触发。

### 🟥 轨道2：见顶做空
- **信号**：①价格≥近期高点×0.97；②RSI≥70；③资金费率>0.05%；④连续3根阴线。满足4个中的3个触发。

### 🟩 轨道3：暴跌后强势反弹（做多）
- **信号**：①从高点回撤≥15%；②RSI≤35；③站上MA10或出现长下影线。满足3个中的3个触发。

## 🔄 数据源降级机制（重点）
1. 首先尝试从 **Hyperliquid** 获取 K 线和资金费率。
2. 如果 Hyperliquid 未上线该币种，自动降级到 **Gate.io 合约 API** 获取。
3. 如果两者均无数据，状态标记为 `unsupported`，邮件中温和提示“暂不支持”，不会导致程序崩溃或报错。

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
修改 `config/config.json` 中的 `watchlist` 数组即可：
```json
"watchlist": [
  { "symbol": "BTC_USDT", "type": "futures" },
  { "symbol": "NEAR_USDT", "type": "futures" }
]

## 🏗️ 项目架构
crypto-monitor/
├── .github/
│ └── workflows/
│ └── monitor.yml # GitHub Actions 定时任务（每30分钟运行）
├── config/
│ ├── config.json # 监控标的与策略配置
│ └── strategies/
│ └── v1_default.json # 策略参数
├── strategies/
│ ├── base.py # 策略接口定义
│ ├── v1_default.py # 三轨并行策略核心逻辑
│ └── loader.py # 动态加载策略
├── monitor.py # 主引擎：数据拉取、策略计算、邮件发送
├── email_sender.py # Gmail 邮件发送模块
├── requirements.txt # 依赖文件
└── README.md

  { "symbol": "ETH_USDT", "type": "futures" },
  { "symbol": "NEAR_USDT", "type": "futures" }
]
