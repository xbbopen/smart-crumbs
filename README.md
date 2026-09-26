# crypto-monitor — 多标的多类型双轨信号监控（GitHub Actions 自动部署）

一个完全免费的加密货币监控项目：每 30 分钟自动监控多个标的（U 本位合约、现货、币安 Alpha 币），
基于「双轨信号」（轨道 A：见顶做空；轨道 B：暴跌抄底）进行判断，触发信号时用 **Gmail 发送 HTML 邮件报告**。

- 完全跑在 GitHub Actions 的免费额度上（每月 2000 分钟，完全够用）
- 纯 Python，不调用任何大模型，不向你收费
- 容错设计：任何数据源失败都不会崩溃，绝不编造数据

---

## 一、项目简介

| 标的类型 | 数据源 | 信号 | 指标 |
|---------|--------|------|------|
| 类型 A：U 本位合约 (futures) | Gate.io 合约 API | 完整双轨（见顶做空 + 暴跌抄底） | K线、资金费率、OI、多空比、ATR、MA10 |
| 类型 B：现货 (spot) | Gate.io 现货 API | 仅做多。见顶改「逃顶预警」，不做空 | K线、成交量、RSI、MACD、ATR、MA10 |
| 类型 C：币安 Alpha (alpha) | 币安 Alpha 公开 API | 仅轨道 B 技术面 | RSI、MACD、ATR、MA10 |
| 补充参考 | Hyperliquid 链上数据 | 参考信号（信号四） | 链上 OI、标记价、资金费率 |

**关键设计**：
- 数据一律用 **Gate.io**（因为 GitHub Actions 是美区 IP，币安 fapi 会返回 451）
- 策略版本化：`config/config.json` 里改 `active_strategy` 即可切换，旧策略永不删除
- 影子模式：`shadow_strategies` 里的策略只计算、只观察，不触发正式警报
- 完全静默规则：默认没有信号时**不发送任何邮件**，避免打扰

---

## 二、目录结构

```
crypto-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml          # GitHub Actions 定时任务（每30分钟）
├── config/
│   ├── config.json              # 主配置（监控列表、策略、标的）
│   └── strategies/
│       ├── registry.json        # 策略注册表（策略名 → 模块/类）
│       ├── v1_default.json      # 默认策略的阈值参数
│       └── v2_optimized.json    # 预留空壳策略参数
├── strategies/
│   ├── __init__.py
│   ├── base.py                  # 指标库 + BaseStrategy 接口
│   ├── v1_default.py            # 默认双轨信号逻辑
│   ├── v2_optimized.py          # 预留空壳（影子模式用）
│   └── loader.py                # 按策略名动态加载
├── monitor.py                   # 主入口（调度、拉数据、发邮件）
├── email_sender.py              # Gmail SMTP 邮件发送
├── requirements.txt
└── README.md
```

---

## 三、如何上传到 GitHub（网页端操作）

### 第 1 步：在 GitHub 创建空仓库
1. 打开 https://github.com/new
2. Repository name 填 `crypto-monitor`
3. 设为 **Private**（包含你的交易监控，建议私有）
4. 不要勾选 "Add a README" / ".gitignore" 等任何初始化项（保持空仓库）
5. 点击 **Create repository**

### 第 2 步：把文件传上去（网页端，3 种方式任选）

**方式 A：直接拖拽上传（最简单）**
1. 在本地把这整个 `crypto-monitor` 文件夹里的 `所有文件` 复制到一个临时文件夹，**注意：不要包含 `logs/`（会自动生成）**
2. 回到你刚创建的仓库页面，点击 **"uploading an existing file"**（上传文件链接）
3. 把文件和文件夹**直接拖进浏览器**这个页面里，点 **Commit changes** 提交即可
   - 拖拽会自动保留 `.github/workflows/` 等子目录结构，放心
   - 隐藏文件（以 `.` 开头）在 Mac 上用 Cmd+Shift+. 显示，Windows 上直接上传即可

**方式 B：逐个 Add file**
- 仓库页面点 **Add file → Upload files**，然后同上拖拽

**方式 C：用 Git 命令行（推荐，以后再改动方便）**
```bash
# 在本地 crypto-monitor 目录执行
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/crypto-monitor.git
git push -u origin main
```

> 提示：把 `logs/` 目录加进一个 `.gitignore` 更好，内容如下（可选）：
> ```
> logs/
> __pycache__/
> *.pyc
> ```

---

## 四、生成 Gmail 应用专用密码（关键步骤）

因为 Gmail 不允许直接用你的登录密码发邮件，必须生成「应用专用密码」：

1. 先开启**两步验证**（很重要，不开启就没有应用专用密码）
   - 打开 https://myaccount.google.com/security
   - 点击「两步验证」→「开始使用」，按提示绑定手机号开启
2. 开启后再打开「应用专用密码」页面：https://myaccount.google.com/apppasswords
   - （如果搜不到，就在上面两步验证页面里搜索「应用专用密码」）
3. 应用名称可以随便填，比如 `crypto-monitor`
4. 页面会给你 **16 位密码**（形如 `abcd efgh ijkl mnop`），**复制保存好**
   - 这 16 位密码就是 `SMTP_PASSWORD`

---

## 五、在 GitHub 仓库设置 Secrets

**一共要设置 5 个 Secret**，其中收件人/服务器可以自己定：

1. 打开你的仓库 → 点 **Settings**（设置）
2. 左侧菜单点 **Secrets and variables → Actions**
3. 点绿色 **New repository secret**，依次添加下面 5 个：

| Secret 名称 | 填什么值 | 示例 |
|------------|---------|------|
| `SMTP_SERVER` | Gmail 服务器，直接填 `smtp.gmail.com` | smtp.gmail.com |
| `SMTP_PORT` | 直接填 `587` | 587 |
| `SMTP_USERNAME` | 你的 Gmail 邮箱完整地址 | you@gmail.com |
| `SMTP_PASSWORD` | 第 4 步生成的 16 位应用专用密码 | abcd efgh ijkl mnop |
| `RECIPIENT_EMAIL` | 收件邮箱（可以就是你自己，也可以是其他邮箱） | you@gmail.com |

每个 Secret 添加完点 **Add secret**，5 个都加好即可。

---

## 六、手动触发工作流测试

1. 仓库页面点 **Actions**（顶部标签）
2. 左侧选择 **多标的合约监控**
3. 点 **Run workflow** 下拉按钮
4. 可选填「临时指定策略版本」，不填就走默认
5. 点绿色 **Run workflow**，运行一次

运行几秒后会开始执行，点进去看日志。如果邮件收到，说明整个链路都通了。

---

## 七、如何添加新标的

编辑 `config/config.json` 里的 `watchlist` 数组，按格式加一条即可：

```json
{
  "symbol": "SOL_USDT",
  "type": "futures",
  "strategy": "v1_default"
}
```

- 合约：Gate 合约符号，形如 `SOL_USDT`、`BTC_USDT`，`type` 填 `futures`
- 现货：Gate 现货交易对，形如 `BTC_USDT`、`ETH_USDT`，`type` 填 `spot`
- 币安 Alpha：币安 Alpha 代币的**裸符号**，如 `UGAS`，`type` 填 `alpha`

改完保存后提交（Commit）到仓库，下次运行会自动生效。

---

## 八、如何切换策略版本

编辑 `config/config.json`：

```json
{
  "active_strategy": "v1_default",        // ← 改成 v2_optimized 即切换
  "shadow_strategies": ["v2_optimized"]
}
```

- `active_strategy`：正式生效的策略
- `shadow_strategies`：影子策略（只计算观察，附在邮件末尾）
- 旧策略文件永不删除，切换只在这一行改，互不影响

---

## 九、常见问题 FAQ

**Q1：邮件一直收不到？**
- 先手动触发一次工作流，看 Actions 日志有没有 `[email] 已发送邮件至 ...`
- 检查 5 个 Secret 是否都填对了，尤其是 `SMTP_PASSWORD`（必须是应用专用密码，不是登录密码）
- 看收件箱/垃圾箱
- 确认 `RECIPIENT_EMAIL` 填的是收件邮箱

**Q2：日志显示数据获取失败？**
- 说明该数据源的免费公开接口暂时不稳定或限流，脚本会自动标记「数据获取失败」并跳过，不会崩溃
- 下一次运行（30 分钟后）会自动重试

**Q3：币安 Alpha 币返回「数据不可用」？**
- 该代币可能不在币安 Alpha 官方列表里，或币安 API 被 451 地理限制
- 脚本会标记「本次跳过」，不影响其他币种

**Q4：会不会占用很多 GitHub 免费额度？**
- 每 30 分钟一次 = 每天 48 次，单次运行约 1~2 分钟内
- GitHub 免费版 Actions 每月 2000 分钟，绰绰有余，**完全免费**

**Q5：没有信号时会不会每天收到一堆空邮件？**
- 不会。默认 `notify_on_no_signal: false`，一个标的都没触发预警/信号时会**静默退出，不发任何邮件**
- 想强制每天收一份例行报告，把它改成 `true` 即可

**Q6：这些信号准不准？**
- 这是基于价格行为 + 链上/合约数据的技术指标监控工具，用于观察提醒
- 所有数字都来自真实 API，脚本内没有任何幻觉数据
- **投资有风险，仅供参考，不构成投资建议**

---

## 十、本地运行（可选）

想先在本地测试，无需 GitHub：

```bash
pip install -r requirements.txt

# 设置环境变量（Windows PowerShell 示例）
$env:SMTP_SERVER="smtp.gmail.com"
$env:SMTP_PORT="587"
$env:SMTP_USERNAME="你@gmail.com"
$env:SMTP_PASSWORD="16位应用专用密码"
$env:RECIPIENT_EMAIL="你@gmail.com"

# 运行
python monitor.py
```

---

MIT License. 本项目仅供学习和数据监控使用。