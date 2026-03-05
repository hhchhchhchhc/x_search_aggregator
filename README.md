# x_search_aggregator

关键词抓取与分析工具：从 X（Twitter）采集内容，生成结构化数据与深度汇总 HTML 文章。

## 功能概览

- 关键词抓取（Latest / Top）
- API 模式与浏览器模式双支持
- 导出 `results.json` / `results.csv`
- 导出 `summary.json` / `summary.md`
- 自动生成排版精美的深度文章 `article.html`

## 脚本说明

- `search_x_api.py`：官方 API 抓取（推荐，稳定）
- `search_x.py`：Playwright + 登录态文件抓取
- `search_with_existing_chrome.py`：连接已有 Chrome（CDP）抓取
- `login_x.py`：登录并保存会话状态
- `html_report.py`：深度分析与 HTML 文章生成

## 环境安装

```bash
cd /home/user/图片/x_search_aggregator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 快速开始

### 方案 A：官方 API（推荐）

1. 在 X Developer Portal 获取 Bearer Token

```bash
export X_BEARER_TOKEN='your_token'
```

2. 抓取 300 条并生成报告

```bash
python search_x_api.py --keyword "信息差" --max-items 300 --lang zh
```

### 方案 B：浏览器登录态抓取

1. 登录并保存状态

```bash
python login_x.py --state auth_state.json --timeout 180
```

2. 执行抓取

```bash
python search_x.py --keyword "信息差" --max-items 300 --sort Latest --state auth_state.json --lang zh --max-scrolls 180 --no-new-stop 10
```

## 输出结果

每次运行生成目录：

```text
output/<关键词>_<时间戳>/
  ├── results.json
  ├── results.csv
  ├── summary.json
  ├── summary.md
  ├── article.html
  └── article_analysis.json
```

## 常见问题

### 抓取到 0 条

- 登录态失效或未登录
- 页面风控/挑战
- 关键词当前结果少

建议优先使用 API 方案；浏览器方案下可重新运行 `login_x.py`。

### Chrome profile 锁（SingletonLock）

说明你的 Chrome 用户目录正被占用。关闭所有 Chrome 进程后重试。

## 安全与合规

- 不要泄露 `auth_token`、`ct0`、`Bearer Token`。
- 泄露后应立刻退出全部会话、改密码、重置 Token。
- 请遵守当地法律法规和平台服务条款。

## 发布到 GitHub

### 1. 创建 GitHub 仓库

在 GitHub 新建空仓库，例如：`x_search_aggregator`。

### 2. 本地提交

```bash
git init
git add .
git commit -m "feat: initial x search aggregator"
```

### 3. 绑定远程并推送

```bash
git branch -M main
git remote add origin https://github.com/<your_user>/x_search_aggregator.git
git push -u origin main
```

如果本地已经是 git 仓库，只执行 `git add/commit/remote/push` 即可。
