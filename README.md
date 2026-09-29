# 🐮 牛来参谋长 · 战情室

> 多周期共振 · 四轨狙击 · 全自动邮件报告

一个部署在 GitHub Actions 上的全自动加密货币监控系统。每 30 分钟扫描一次自选标的池，通过 1D / 4H / 1H / 30m 四周期共振，捕捉五种高胜率机会，触发信号后自动生成图文报告并通过邮件推送。

数据源采用 **Hyperliquid** 公开 API，无需 API Key，无 IP 地理封锁。

---

## 🎯 核心能力

系统从「市场状态 → 轨道信号 → 入场计划」三层漏斗过滤，只在大方向明确、指标共振、形态配合时才出手：

1. **底部突破** —— 底部震荡后的放量启动
2. **见顶做空** —— 高位衰竭后的反转
3. **顺势做空** —— 下跌中继中的反弹做空
4. **暴跌反弹** —— 极端恐慌后的短线做多
5. **趋势回踩** —— 强势趋势中的回调加仓

报告面向币安广场读者，中文化 + IP 化 + 话术随机化，每期标题不重复。

---

## 🏗️ 项目架构
牛来参谋长 · 战情室/
│
├── .github/workflows/ # GitHub Actions 流水线
│ ├── bootstrap.yml # 历史数据初始化（手动触发一次）
│ ├── monitor.yml # 实时监控（每 30 分钟自动运行）
│ └── backtest.yml # 历史回测（手动触发，可选日期区间）
│
├── config/
│ └── config.json # 自选标的池 + 策略开关
│
├── strategies/
│ ├── base.py # 策略基类 + 通用技术指标工具
│ ├── v1_default.py # 主策略（市场状态机 + 四轨道评估）
│ ├── v2_optimized.py # 预留空壳（未启用）
│ └── loader.py # 策略动态加载器
│
├── data/ # SQLite 数据库目录（git 忽略）
├── logs/ # 信号日志 + 标题去重历史（git 忽略）
│
├── data_fetcher.py # 数据获取 + 技术指标计算 + 源降级
├── data_db.py # SQLite 数据库封装
├── data_bootstrap.py # 首次历史数据拉取脚本
│
├── market_scanner.py # 市场热点扫描（供报告顶部渲染）
├── report_lexicon.py # 中文词典 + 标题/解读话术池
├── report_builder.py # 邮件报告 HTML 生成
├── email_sender.py # 邮件发送（SMTP + Resend 双通道）
│
├── monitor.py # 实时监控主入口
├── backtest.py # 历史回测引擎
│
├── requirements.txt
└── README.md


---

## 🔄 数据流
┌─────────────────────┐
│ config.json │
│ watchlist (27 标的)│
└──────────┬──────────┘
│
┌──────────▼──────────┐
│ monitor.py │
│ 逐标的调度 │
└──────────┬──────────┘
│
┌──────────▼──────────┐
│ data_fetcher.py │
│ · 源优先级： │
│ Hyperliquid │
│ → Binance │
│ → Gate.io │
│ · 指标计算：RSI、 │
│ MACD、KDJ、 │
│ BOLL、ATR、ADX │
└──────────┬──────────┘
│
┌──────────▼──────────┐
│ data_db.py │
│ SQLite 缓存 K 线 │
└──────────┬──────────┘
│
┌──────────▼──────────┐
│ v1_default.py │
│ 市场状态机 + 四轨 │
│ 评估 + 入场计划 │
└──────────┬──────────┘
│
┌────────────────┼─────────────────┐
│ │ │
┌─────────▼────────┐ ┌────▼──────────┐ ┌───▼──────────────┐
│ market_scanner │ │ report_lexicon│ │ report_builder │
│ 热点扫描 │ │ 词典+话术池 │ │ HTML 渲染 │
└─────────┬────────┘ └────┬──────────┘ └───┬──────────────┘
│ │ │
└────────────────┼─────────────────┘
│
┌──────────▼──────────┐
│ email_sender.py │
│ 邮件推送 │
└─────────────────────┘

---

## 📊 策略逻辑拆解

### 市场状态机（Regime）

策略第一步不是直接找信号，而是**先判断当前市场处于什么状态**。不同状态下允许出手的轨道不同：

| 市场状态 | 判定条件 | 允许轨道 |
|---|---|---|
| ⏸️ 动量停滞 | MACD柱≈0 且 ADX<20 | 仅暴跌反弹 |
| 🟢🟢 强多头 | 1D多头 + 4H强/温和多头 | 底部突破、趋势回踩 |
| 🟢 弱多头 | 1D多头 + 4H震荡 | 趋势回踩、见顶做空 |
| 🚨 弱多头警告 | 1D多头 + 4H空头 | 仅做空 |
| ⚪ 震荡整理 | 无明确方向 | 做空、暴跌反弹 |
| 🔴 弱空头 | 1D空头 + 4H非空 | 仅做空 |
| 🔴🔴 强空头 | 1D空头 + 4H空头 | 仅做空 |

这个机制**防止逆势操作**：比如强空头里不允许做多，避免"抄底抄到半山腰"。

### 四轨道评分

每个轨道独立计分，达标后再进入"最终裁决"。

#### 🚀 轨道1：底部突破做多

**前置硬条件**：1D多头 + 4H多头 + ADX≥22 + 距近期高点 ≥10%

**6 条评分**（≥4 分触发）：
1. 价格接近近期低点（≤低点×1.05）
2. 站上 30m MA10
3. 30m RSI 在 45-65 或 1H RSI < 40
4. 30m 放量阳线（量 > 前 5 根均量×1.3）
5. 1H KDJ J < 20
6. 4H MACD 金叉

#### 🔪 轨道2：做空（双子类型）

**2A 见顶做空（反转，≥4/6 触发）**：
1. 逼近近期高点（≥高点×0.97）
2. 30m RSI ≥ 70
3. 30m CVD 顶背离
4. 1H RSI 超买 / 顶背离 / 费率拥挤度 > 50%
5. 1H KDJ J > 100
6. 4H MACD 死叉

**2B 顺势做空（延续，核心 2/2 + 辅助 ≥2/4）**：
- 核心：4H EMA20 < EMA50 + 价格接近/高于布林中轨
- 辅助：1H RSI 中位 + 30m 长上影/看跌吞没 + 1H MACD 空头 + 30m RSI < 60

#### 🩸 轨道3：暴跌反弹做多

**前置硬条件**：1D非空 + ADX≥12

**特殊保护**：4H 处于空头时，仅当"极端超卖"（RSI_4H<20 且 RSI_30m<25 且 KDJ J<-10 且 RSI底背离）才允许出手。

**6 条评分**（≥4 触发）：
1. 从高点回撤 ≥ 15%
2. 30m RSI ≤ 35
3. 30m CVD 牛背离
4. 1H RSI 超卖或底背离
5. 1H KDJ J < 0
6. 30m 止跌形态（长下影）

#### 🎯 轨道4：趋势回踩做多

**前置硬条件**：1D多头 + 4H多头 + ADX≥18

**6 条评分**（≥4 触发）：
1. 价格回踩 1H 布林中轨（**动态容差：1.5×ATR%，限制在 1%-4%**）
2. 缩量回踩（量 < 前 5 根均量×0.8）
3. 30m 止跌形态
4. 1H RSI 在 40-55
5. 站上 30m MA10
6. 4H MACD 未死叉

### 冲突优先级

当一个标的多个轨道同时达标时，按下列优先级裁决：
做空 > 趋势回踩 > 暴跌反弹 > 底部突破

被否决的轨道会在报告里显示，方便复盘。

---

## 🛡️ 风险控制

### 分层 ATR 止损

止损倍数**按币种流动性分层**，避免高波动 altcoin 被正常波动扫损：

| 币种分类 | 止损倍数 | 示例 |
|---|---|---|
| 主流币 | 1.5 × ATR | BTC、ETH |
| 中市值 | 2.0 × ATR | SOL、BNB、XRP、ADA、AVAX、LINK、DOGE |
| 高波动 altcoin | 2.5 × ATR | 其余所有 |

### 2% 资金管理

固定本金 10000 U，单笔最大亏损 200 U，按止损空间反推仓位：
仓位 = 200 U ÷ 止损空间%

### 分档入场

每个轨道给出 2-3 档入场计划，包含权重、类型（市价/限价）、价格。例如趋势回踩通常是"60%市价 + 40%限价"；暴跌反弹则全是限价单，避免抄在半山腰。

### 三段式移动止盈

- 盈利 1×ATR → 止损移至成本价（保本）
- 盈利 2×ATR → 止损移至 1×ATR 处
- 盈利 3×ATR → 止损移至 2×ATR 处，止盈 50% 仓位

配合 MA10 动态离场线，趋势破了就走。

---

## 📧 报告特性

- **中文全覆盖**：报告内除交易对符号外，无任何英文术语暴露
- **热点开场**：报告顶部展示"本期市场焦点"卡片
  - 领涨/领跌 Top 3
  - RSI 极端（贪婪/恐慌）
  - 成交量异动
  - 资金费率极端
  - 逼近前高/前低
- **多周期共振面板**：每个标的顶部一张表，一览 1D / 4H / 1H / 30m 的趋势、RSI、关键位
- **标题 IP 锚定**：每条标题必带"参谋长"或"牛来参谋长"
- **标题去重**：最近 20 条标题不重复（`logs/subject_history.json`）
- **话术随机化**：每个信号方向 8 套模板，随机抽取 + 嵌入实时数据
- **时段识别**：按北京时间分早盘/午盘/欧盘/美盘/深夜，语气不同

---

## 🚀 部署指南

### 1. 上传到 GitHub

```bash
git init
git add .
git commit -m "init"
git remote add origin <your-repo-url>
git push -u origin main
2. 配置 Gmail 应用专用密码
Google 账号 → 安全 → 开启两步验证

访问 https://myaccount.google.com/apppasswords

生成 16 位应用专用密码（立即复制，只显示一次）

⚠️ 重置 Gmail 主密码后，旧的应用专用密码会被自动撤销，需要重新生成。

3. 配置 GitHub Secrets
仓库 → Settings → Secrets and variables → Actions：

Secret	值
SMTP_SERVER	smtp.gmail.com
SMTP_PORT	587（系统会自动降级尝试 465）
SMTP_USERNAME	你的 Gmail
SMTP_PASSWORD	16 位应用专用密码（不带空格）
RECIPIENT_EMAIL	接收邮箱
4.（可选，推荐）配置 Resend API
GitHub Actions 访问 Gmail SMTP 偶发超时，可用 Resend 兜底：

注册 https://resend.com（免费 3000 封/月）

获取 API Key

新增 Secret：RESEND_API_KEY

设置后系统会优先走 Resend，失败才降级到 SMTP。

5. 初始化历史数据
Actions → 历史数据初始化 → Run workflow

约 5-10 分钟，完成后数据库会存入 GitHub Actions Cache。

6. 启用实时监控
Actions → 多标的的合约监控 → Run workflow

之后每 30 分钟自动运行。

🛠️ 常用操作
添加/删除监控标的
编辑 config/config.json：

json
"watchlist": [
  { "symbol": "BTC_USDT", "type": "futures" },
  { "symbol": "ZEC_USDT", "type": "futures" },
  { "symbol": "PEPE_USDT", "type": "spot" }
]
type: futures → 优先走 Hyperliquid 合约，无对应合约则降级现货

type: spot → 直接走现货（币安优先，失败降级 Gate）

运行历史回测
Actions → 历史回测 → Run workflow，可选参数：

strategies：策略名（默认 v1_default）

symbols：标的（默认 BTC ETH SOL）

start_date / end_date：YYYY-MM-DD

asset_type：futures / spot

回测报告通过邮件发送，包含总览表、每标的详细分析、最近 10 笔交易明细、S/A/B/C/D 评级。

本地测试
pip install -r requirements.txt

# 拉取历史数据
python data_bootstrap.py --from-config --intervals 30m 4h

# 单次监控
export SMTP_SERVER=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USERNAME=your@gmail.com
export SMTP_PASSWORD="xxxx xxxx xxxx xxxx"
export RECIPIENT_EMAIL=to@example.com
python monitor.py

# 本地回测
python backtest.py --strategies v1_default --symbols BTC ETH

🧩 模块详解
模块	职责
data_fetcher.py	多源拉取 K 线（Hyperliquid → Binance → Gate 瀑布降级）；计算 RSI/MACD/KDJ/BOLL/ATR/ADX；聚合 1H/4H/1D
data_db.py	SQLite 读写；K 线去重存储；按 (symbol, interval, timestamp) 主键；提供 VACUUM 压缩
data_bootstrap.py	首次运行拉取历史 K 线（30m 500 根 + 4h 2000 根）；支持按 config 批量初始化
strategies/base.py	策略基类；通用技术指标纯函数（供子类扩展时使用）
strategies/v1_default.py	主策略：市场状态机 + 四轨道评估 + 分层止损 + 分档入场
strategies/loader.py	动态加载策略模块（v1_default → V1DefaultStrategy）
market_scanner.py	遍历所有标的，输出领涨/领跌/极端RSI/爆量/费率/逼近关键位 Top 3
report_lexicon.py	中文词典（趋势/动能/状态/时段）+ 标题池 + 解读话术池
report_builder.py	HTML 报告生成：热点卡片 + 全景图 + 每标的独立卡片 + 术语词典 + 免责声明
email_sender.py	邮件发送：优先 Resend API，兜底 SMTP（465 SSL / 587 STARTTLS），3 次重试
monitor.py	主入口：调度 → 拉数据 → 跑策略 → 生成报告 → 发邮件 → 写日志
backtest.py	回测引擎：复用 data_fetcher 与 data_db，逐根 K 线回放，输出绩效指标
🔒 稳定性设计
系统在多层做了兜底，确保单个环节出错不会影响整体：

数据源瀑布降级：Hyperliquid 失败 → Binance → Gate，任一可用即成功

API 429 指数退避：3 次重试，避免限流被封

策略异常隔离：单个标的崩溃不影响其他标的，报告里显式显示错误卡片

报告渲染兜底：单卡片渲染失败时降级为错误提示，不阻塞其他标的

邮件发送重试：3 次重试 + 端口降级 + Resend 兜底

失败显式化：邮件失败让 job 明确标红，日志给出修复指引

标题去重降级：历史文件读不到就用随机，写失败静默忽略

话术填充兜底：占位符缺失时保留原模板，不抛异常

📖 术语表
术语	说明
多周期共振	1D 定方向，4H 定趋势，1H 找时机，30m 扣扳机
ADX	趋势强度指标。≥25 强趋势，<20 震荡
CVD	累积成交量差。用于检测量价背离
RSI	相对强弱。>70 超买，<30 超卖
KDJ J 值	短期超买超卖。<0 超卖，>100 超买
ATR	平均真实波幅，用于止损和仓位
布林中轨	1H 的 MA20，趋势回踩经典支撑
资金费率	Hyperliquid 每小时结算，正常 0.001-0.003%，>0.01% 极端
拥挤度	资金费率在历史分布中的百分位
⚠️ 免责声明
本项目仅供学习和交流使用，不构成任何投资建议。加密货币交易具有极高风险，可能导致全部本金损失。请自行判断并承担交易后果。
