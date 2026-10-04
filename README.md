# DASEIN 晨间情报内参 · Morning Intelligence Briefing

> **自动化资产双轴量化 · 全球政经宏观 · 前沿 AI 科技 · 《经济学人》双语精读**  
> 每日定时推送到 QQ 邮箱（`2158793923@qq.com`），Serverless 零服务器成本架构。

---

## 🏛️ 系统架构 (Serverless 闭环)

```
Cloudflare Workers Cron (工作日 08:30 / 09:00 北京时间定时触发)
   ↓ 调用 GitHub Actions Dispatch API (免费云端算力)
GitHub Actions 执行 index_monitor.py + briefing_content.py
   ├─ ① 资产双轴层: 纳指100 / 标普500 / 沪深300 / 中证500 (PE/PB/分位/SMA200)
   ├─ ② 宏观指标层: 10年期美债收益率 (^TNX) / COMEX黄金 (GC=F) / 费城半导体 (SOXX)
   ├─ ③ 前沿科技层: Hugging Face Daily Papers (高票 AI 论文与架构突破)
   ├─ ④ 全球政经层: CNBC Economy & 央行宏观要闻 RSS
   └─ ⑤ 英语精读层: 《经济学人》(The Economist) 双语金句精读与高阶词汇解析
   ↓
高颜值响应式 HTML 邮件渲染引擎 ➔ QQ 邮箱推送
```

- **零服务器成本：** 触发端运行在 Cloudflare Workers，执行端运行在 GitHub Actions，发信端使用 QQ 邮箱 SMTP。
- **高安全与隔离：** `SMTP_USER`、`SMTP_CODE` 均存入 GitHub Actions Secrets，代码库无任何硬编码明文密钥。
- **高鲁棒性设计：** 纯 Python 标准库构建，无需复杂外部依赖；全模块内置优雅降级与网络波动熔断保护。

---

## 📊 核心模块与规则设计

### 1. 核心指数双轴模型 (估值管定投，趋势管止盈)
* **估值评分 ➔ 新资金投入比例（仓位调节器）：**
  * 评分 `< 40%` ➔ **100% 定投**（低估加码）
  * 评分 `40%~60%` ➔ **75% 定投**
  * 评分 `60%~75%` ➔ **50% 定投**
  * 评分 `75%~85%` ➔ **25% 定投**
  * 评分 `≥ 85%` ➔ **0% 定投**（高估暂停新钱）
* **趋势确认 ➔ 存量严格止盈（买卖开关）：**
  * 评分 `≥ 95%` 或（评分 `≥ 90%` 且收盘连续 2 日跌破 SMA200 且 20 日斜率为负）➔ **触发分批止盈**（每次 1/3，间隔 10% 涨幅），卖出后继续定投。

### 2. 全球宏观核心风向标
* 监控 10年期美债基准利率、国际现货黄金及费城半导体 ETF 涨跌，辅助研判无风险收益率与科技成长股流动性环境。

### 3. 前沿 AI 科技与 Agent 态势
* 追踪当日社区高票热门 AI 研究论文，萃取核心价值标签（模型架构、低延迟边缘计算、智能体推理）。

### 4. 《经济学人》双语精读 (The Economist)
* 严格遵循双语分块规范：
  * **PART 1：** 原版英文语段（精选宏观经济、技术哲学与系统科学）。
  * **水平分割线 (`---`)**
  * **PART 2：** 专业学术译文 + CET-4/考研高频词汇搭配 + 长难句语法拆解。

---

## 💻 本地运行与调试

```bash
# 运行完整监控与早报生成（若未配置 SMTP 环境变量，将打印本地文本报告并跳过发信）
python index_monitor.py

# 详细调试模式
python index_monitor.py --verbose
```

---

## ⚙️ 环境变量配置

在 GitHub 仓库的 `Settings ➔ Secrets and variables ➔ Actions` 中添加：
* `SMTP_USER`: 发信 QQ 邮箱账号（如 `2158793923@qq.com`）
* `SMTP_CODE`: QQ 邮箱开启 SMTP 服务的 16 位授权码（非 QQ 密码）
