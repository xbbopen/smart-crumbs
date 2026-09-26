# 多标的合约三轨监控系统

基于 GitHub Actions 的全自动合约行情监控系统，专为捕捉极端行情设计。数据源采用 **Hyperliquid** 公开 API，无需 API Key，无 IP 地理封锁。

## 🎯 核心目标

捕捉现货及合约市场中三种极端机会：
1. **底部震荡后的稳定上行**（轨道1：做多）
2. **见顶后的做空机会**（轨道2：做空）
3. **见顶后暴跌的强势反弹**（轨道3：做多）

同时严格遵循《以交易为生》的交易纪律，在信号触发时自动计算：**入场价、硬止损价、2%规则仓位上限、移动止盈价格**。

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

## 📊 三轨策略逻辑详解

所有数据基于 **30分钟 K线**（共300根，约6天历史），每30分钟自动运行一次。

### 🟩 轨道1：底部震荡突破（做多）
- **逻辑**：抓取在底部充分震荡后，站上支撑位的稳定上行阶段。
- **信号**：
  1. 价格处于近期低点附近（≤ 低点 × 1.05）
  2. 价格站上 MA10
  3. RSI 处于温和上升区（45 - 65）
  4. 成交量较前5根K线均值放大 1.2 倍
- **触发**：满足4个中的3个。

### 🟥 轨道2：见顶做空
- **逻辑**：抓取趋势末端，多头力竭的见顶信号。
- **信号**：
  1. 价格逼近近期高点（≥ 高点 × 0.97）
  2. RSI 超买（≥ 70）
  3. 资金费率过高（Hyperliquid 每小时结算，阈值 > 0.01%）
  4. 连续3根30分钟K线收阴
- **触发**：满足4个中的3个。

### 🟩 轨道3：暴跌后强势反弹（做多）
- **逻辑**：抓取见顶后急跌，多头开始反扑的反弹阶段。
- **信号**：
  1. 价格从高点回撤 ≥ 15%
  2. RSI 超卖（≤ 35）
  3. 企稳信号（站上MA10 或 出现长下影线）
- **触发**：满足3个中的3个。

## 📐 《以交易为生》交易计划

一旦触发信号，邮件会附带具体交易计划：
- **硬止损价**：做多设在近期低点 - 1.5×ATR；做空设在近期高点 + 1.5×ATR。
- **2%资金管理**：根据止损空间，计算单笔交易最大仓位（总资金亏损不超过 2%）。
- **移动止盈**：浮盈20%时保护成本，浮盈50%时锁定利润。
- **MA10 动态离场**：跌破（或涨破）MA10 时清仓剩余仓位。

## ⚙️ 部署指南（GitHub Actions）

### 1. 创建 GitHub 仓库
将本项目上传到 GitHub 仓库（建议设为 Private 或 Public）。

### 2. 配置 Gmail 应用密码
1. 登录 Google 账号 → 安全性 → 开启“两步验证”。
2. 搜索“应用专用密码”，创建一个名为 `Crypto Monitor` 的密码，获取16位字符串（去掉空格备用）。

### 3. 配置 GitHub Secrets
进入仓库 → Settings → Secrets and variables → Actions → 新建以下 Secrets：
- `SMTP_SERVER`: `smtp.gmail.com`
- `SMTP_PORT`: `587`
- `SMTP_USERNAME`: 你的Gmail地址
- `SMTP_PASSWORD`: 上一步获取的16位应用密码
- `RECIPIENT_EMAIL`: 接收报告的邮箱

### 4. 触发运行
进入 Actions 页面 → 选择“多标的的合约监控” → 点击 `Run workflow`。

## 🛠️ 如何添加或删除监控标的？

修改 `config/config.json` 中的 `watchlist` 数组：
```json
"watchlist": [
  { "symbol": "BTC_USDT", "type": "futures" },
  { "symbol": "ETH_USDT", "type": "futures" },
  { "symbol": "NEAR_USDT", "type": "futures" }
]
