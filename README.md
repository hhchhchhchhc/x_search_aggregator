<p align="center">
  <h1 align="center">🔍 X Search Aggregator</h1>
  <p align="center">
    <b>全自动 X (Twitter) 数据采集 & 分析工具集</b><br/>
    关键词搜索 · 用户历史推文 · 关注列表 · 关注者动态 · 一键500条
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python"/>
    <img src="https://img.shields.io/badge/Playwright-自动化-2EAD33?logo=playwright&logoColor=white" alt="Playwright"/>
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License"/>
    <img src="https://img.shields.io/badge/Platform-X.com-000000?logo=x&logoColor=white" alt="X.com"/>
  </p>
</p>

---

## ✨ 功能亮点

| 功能 | 脚本 | 说明 |
|------|------|------|
| 🔎 **关键词搜索** | `search_x.py` | 按关键词搜索 X，自定义数量，支持语言过滤 |
| 🔎 **关键词搜索 Top 500** | `search_keyword_500.py` | 一键抓取关键词最新 **500 条**推文 |
| 📜 **用户历史推文** | `crawl_user_timeline.py` | 爬取任意用户的全部历史推文 |
| 👥 **用户关注列表** | `crawl_user_following.py` | 通过内部 API 爬取用户完整关注列表 |
| 📡 **关注者最新动态 500** | `crawl_following_timeline_500.py` | 一键抓取你关注的所有人的最新 **500 条**动态 |
| 🔑 **登录状态管理** | `login_x.py` | 一次登录，持久化 session，后续全自动 |

> **无需 X 开发者账号**，基于浏览器已登录会话自动化采集，零 API 配额限制。

---

## 🚀 快速开始

### 1. 安装（一次性）

```bash
git clone https://github.com/hhchhchhchhc/x_search_aggregator.git
cd x_search_aggregator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. 登录 X（一次性）

```bash
python login_x.py --state auth_state.json --timeout 180
```

> 会打开浏览器窗口，手动完成登录即可，登录状态自动保存。

### 3. 开始使用

#### 🔎 关键词搜索最新 500 条

```bash
python search_keyword_500.py --keyword "AI Agent" --lang zh
```

#### 📡 关注者最新 500 条动态

```bash
python crawl_following_timeline_500.py
```

#### 🔎 关键词搜索（自定义数量）

```bash
python search_x.py --keyword "信息差" --max-items 300 --sort Latest --lang zh
```

#### 📜 爬取用户历史推文

```bash
python crawl_user_timeline.py --user-url "https://x.com/elonmusk" --max-items 0
```

> `--max-items 0` = 不设上限，尽量抓全部历史。

#### 👥 爬取用户关注列表

```bash
python crawl_user_following.py --user-url "https://x.com/elonmusk" --max-items 0
```

---

## 📊 输出示例

每次运行自动生成独立输出目录，包含多种格式：

```
output/AI_Agent_500_20260306_180318/
├── results.json          # 全量结构化数据
├── results.csv           # Excel 可直接打开
├── summary.json          # 统计摘要（热门标签/提及/高赞推文）
├── summary.md            # Markdown 版摘要
└── article.html          # 可视化 HTML 报告，浏览器直接打开
```

**关注者动态**额外输出：

```
output/following_timeline_500_20260306_193541/
├── results.json / csv
├── summary.json / md
├── summary.html          # 关注者活跃度排名、热门标签、Top 推文
└── article.html          # 完整动态文章
```

**用户关注列表**额外输出：

```
output/following_vista8_20260306/
├── results.json / csv
├── detailed_report.json  # 关注者画像分析（认证比例/Bio关键词/语言分布）
├── detailed_report.md
└── detailed_report.html
```

---

## 🛠️ 全部参数速查

### search_keyword_500.py

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | *必填* | 搜索关键词 |
| `--lang` | 空 | 语言过滤（`zh` / `en` 等） |
| `--headless` | 关 | 无头模式运行 |
| `--max-scrolls` | 200 | 最大滚动轮数 |
| `--no-new-stop` | 10 | 连续无新内容后停止 |

### crawl_following_timeline_500.py

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--headless` | 关 | 无头模式运行 |
| `--max-scrolls` | 300 | 最大滚动轮数 |
| `--no-new-stop` | 12 | 连续无新内容后停止 |

### search_x.py

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | *必填* | 搜索关键词 |
| `--max-items` | 200 | 最大采集数 |
| `--sort` | Latest | 排序方式（`Top` / `Latest`） |
| `--lang` | 空 | 语言过滤 |
| `--headless` | 关 | 无头模式 |

### crawl_user_timeline.py

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--user-url` | *必填* | 用户主页 URL |
| `--max-items` | 0 | 最大条数（0=不限） |
| `--max-scrolls` | 1000 | 最大滚动轮数 |
| `--with-replies` | 关 | 包含回复推文 |

### crawl_user_following.py

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--user-url` | *必填* | 用户主页 URL |
| `--max-items` | 0 | 最大条数（0=不限） |
| `--max-pages` | 200 | 最大 API 翻页数 |

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────┐
│              X Search Aggregator            │
├─────────────┬───────────────────────────────┤
│  采集层     │  Playwright 浏览器自动化       │
│             │  ├─ 模拟真实用户滚动浏览       │
│             │  ├─ 反检测指纹 (browser_config) │
│             │  └─ 内部 GraphQL API 调用      │
├─────────────┼───────────────────────────────┤
│  解析层     │  DOM 解析 + 多级回退策略        │
│             │  ├─ 推文内容/时间/指标提取      │
│             │  ├─ 用户信息提取               │
│             │  └─ 智能去重 (tweet_id)        │
├─────────────┼───────────────────────────────┤
│  输出层     │  JSON / CSV / Markdown / HTML  │
│             │  ├─ 统计摘要 & 可视化报告      │
│             │  └─ 热门标签/提及/高赞排名     │
└─────────────┴───────────────────────────────┘
```

---

## ❓ 常见问题

<details>
<summary><b>抓取到 0 条怎么办？</b></summary>

1. **登录态过期** → 重新执行 `python login_x.py`
2. **平台风控** → 稍后重试或更换网络环境
3. **参数过小** → 增大 `--max-scrolls` 和 `--no-new-stop`
</details>

<details>
<summary><b>关注列表没抓全？</b></summary>

这是 X 平台侧可见性限制，非脚本问题。查看 `detailed_report.json` 中的覆盖率：
- `profile_following_count`：账号显示的关注数
- `total_following_collected`：实际抓到的数量
- `coverage_ratio`：覆盖比例
</details>

<details>
<summary><b>需要 X 开发者账号吗？</b></summary>

**不需要！** 主要脚本全部基于浏览器会话自动化，无需任何 API Key。

仅 `search_x_api.py` 需要 Bearer Token（官方 API 方案，可选）。
</details>

<details>
<summary><b>支持无头模式吗？</b></summary>

所有脚本都支持 `--headless` 参数，适合服务器/后台运行。
</details>

---

## 📁 项目结构

```
x_search_aggregator/
├── search_keyword_500.py          # ⭐ 关键词搜索 500 条
├── crawl_following_timeline_500.py # ⭐ 关注者动态 500 条
├── search_x.py                    # 关键词搜索（自定义数量）
├── crawl_user_timeline.py         # 用户历史推文爬取
├── crawl_user_following.py        # 用户关注列表爬取
├── search_x_api.py                # 官方 API 搜索（可选）
├── login_x.py                     # 登录状态保存
├── html_report.py                 # HTML 报告生成引擎
├── browser_config.py              # 反检测浏览器配置
├── search_x_long_runner.py        # 长时间运行采集器
├── requirements.txt
└── output/                        # 所有输出自动归档于此
```

---

## 🤝 贡献

欢迎 Issue 和 PR！如果觉得有用，请点个 ⭐ Star 支持一下。

---

## 👥 AI 超级个体效率工具分享群

> 群聊：AI 超级个体效率工具分享群 3

![群聊二维码](assets/ai-super-individual-tools-group3-qr.jpg)

该二维码 7 天内有效，失效后会更新。

---

## 🔥 港美掘金 · 实战派财富系统

我正在「港美掘金」和朋友们讨论有趣的话题，你一起来吧？
👉 https://t.zsxq.com/eQkvu

---

<p align="center">
  <sub>Made with ❤️ for the X/Twitter research community</sub>
</p>
