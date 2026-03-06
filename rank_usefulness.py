#!/usr/bin/env python3
"""对采集结果按有用程度综合评分排名，生成精美 HTML 排名页面。

可对任意 results.json 使用，包括关键词搜索、关注者动态、用户历史推文等。

评分规则：
  - 互动加权：Like×3 + RT×5 + Reply×2 + Bookmark×8
  - 浏览量归一化加分
  - 内容长度加分（鼓励有实质内容）
  - 技术/行业关键词命中加分
  - 结构化内容（步骤/列表）加分
  - 含资源链接加分
  - 垃圾营销词扣分
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# ── 评分相关常量 ──

TECH_KEYWORDS = [
    "AI", "GPT", "Claude", "API", "Python", "GitHub", "策略", "工具",
    "教程", "方法", "框架", "模型", "数据", "开源", "量化", "分析",
    "Prompt", "Agent", "LLM", "RAG", "MCP", "Cursor", "代码", "自动化",
    "Web3", "Crypto", "DeFi", "NFT", "Bitcoin", "Ethereum", "区块链",
    "投资", "套利", "收益", "研报", "持仓", "对冲", "Alpha",
    "Docker", "Kubernetes", "Linux", "Rust", "TypeScript", "React",
    "产品", "增长", "变现", "创业", "效率", "认知", "思维",
]

STRUCT_MARKERS = ["1.", "2.", "①", "②", "第一", "第二", "•", "- ", "步骤", "方法"]

SPAM_SIGNALS = ["关注我", "点赞转发", "抽奖", "免费领", "私信我", "互粉", "刷粉"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对采集结果按有用程度排名，生成 HTML 页面",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python rank_usefulness.py --input output/following_timeline_500_20260306_193541
  python rank_usefulness.py --input output/信息差_500_20260306_180318/results.json
  python rank_usefulness.py --input output/user_elonmusk_20260306/results.json --title "Elon Musk 推文价值排名"
        """,
    )
    parser.add_argument(
        "--input", required=True,
        help="results.json 文件路径，或包含 results.json 的目录路径",
    )
    parser.add_argument("--title", default="", help="自定义页面标题（默认自动生成）")
    parser.add_argument(
        "--output", default="",
        help="输出 HTML 文件路径（默认写入同目录下 usefulness_ranking.html）",
    )
    return parser.parse_args()


# ── 评分算法 ──

def usefulness_score(item: Dict) -> float:
    """综合评分：互动加权 + 内容质量信号。"""
    likes = int(item.get("like_count", 0))
    rts = int(item.get("retweet_count", 0))
    reps = int(item.get("reply_count", 0))
    bmarks = int(item.get("bookmark_count", 0))
    views = int(item.get("view_count", 0))
    text = (item.get("text") or "").strip()
    text_len = len(text)

    # 互动分（加权）
    engagement = likes * 3 + rts * 5 + reps * 2 + bmarks * 8

    # 浏览量加分（归一化，上限 50）
    view_bonus = min(views / 1000, 50) if views > 0 else 0

    # 内容长度加分（上限 30）
    length_bonus = min(text_len / 20, 30)

    # 内容质量信号
    quality_bonus = 0.0

    # 含链接 → 可能是资源分享
    if "http" in text:
        quality_bonus += 5

    # 技术/行业关键词命中
    text_lower = text.lower()
    keyword_hits = sum(1 for kw in TECH_KEYWORDS if kw.lower() in text_lower)
    quality_bonus += keyword_hits * 3

    # 结构化内容（步骤/列表）
    if any(marker in text for marker in STRUCT_MARKERS):
        quality_bonus += 8

    # 惩罚：垃圾营销词
    spam_penalty = sum(5 for s in SPAM_SIGNALS if s in text)

    return engagement + view_bonus + length_bonus + quality_bonus - spam_penalty


def score_badge(score: float) -> tuple[str, str]:
    """根据分数返回 (徽章文字, 颜色)。"""
    if score >= 100:
        return "🔥🔥🔥", "#e74c3c"
    elif score >= 50:
        return "🔥🔥", "#e67e22"
    elif score >= 20:
        return "🔥", "#f39c12"
    else:
        return "·", "#95a5a6"


# ── HTML 生成 ──

def esc(s) -> str:
    return html_mod.escape(str(s or ""))


def fmt_text(text: str) -> str:
    """格式化推文文本：转义 HTML + 换行 + 高亮链接。"""
    t = esc(text)
    t = t.replace("\n", "<br/>")
    t = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', t)
    return t


def build_ranking_html(items: List[Dict], title: str) -> str:
    """构建完整 HTML 页面。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(items)
    high_quality = sum(1 for i in items if i["_score"] >= 50)
    unique_authors = len(set(i.get("user_handle", "") for i in items))

    # Top 30 中最活跃作者
    top_authors: Counter = Counter()
    for i in items[:30]:
        h = i.get("user_handle", "")
        if h:
            top_authors[h] += 1
    top_authors_html = " ".join(
        f'<span class="tag">@{esc(a)} ({c})</span>'
        for a, c in top_authors.most_common(10)
    )

    # 构建每条推文卡片
    rows_html = []
    for rank, item in enumerate(items, 1):
        score = item["_score"]
        badge, badge_color = score_badge(score)
        text_html = fmt_text(item.get("text", ""))
        posted = (item.get("posted_at") or "")[:19].replace("T", " ")
        handle = esc(item.get("user_handle", ""))
        name = esc(item.get("user_name", ""))
        url = esc(item.get("url", ""))
        likes = item.get("like_count", 0)
        rts = item.get("retweet_count", 0)
        reps = item.get("reply_count", 0)
        bmarks = item.get("bookmark_count", 0)

        rows_html.append(f"""
    <div class="tweet-card" style="border-left: 4px solid {badge_color};">
      <div class="tweet-header">
        <span class="rank">#{rank}</span>
        <span class="score" style="background:{badge_color};">{badge} {score:.0f}分</span>
        <a class="author" href="https://x.com/{handle}" target="_blank">@{handle}</a>
        <span class="name">{name}</span>
        <span class="time">{posted}</span>
      </div>
      <div class="tweet-body">{text_html}</div>
      <div class="tweet-metrics">
        <span>❤️ {likes}</span>
        <span>🔁 {rts}</span>
        <span>💬 {reps}</span>
        <span>🔖 {bmarks}</span>
        <a href="{url}" target="_blank" class="link-btn">查看原文 →</a>
      </div>
    </div>""")

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(title)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "SF Pro Display", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
    color: #e0e0e0;
    min-height: 100vh;
  }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 24px 16px; }}
  .page-header {{
    text-align: center; padding: 40px 20px 30px;
    background: rgba(255,255,255,0.05); border-radius: 16px;
    margin-bottom: 24px; backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.1);
  }}
  .page-header h1 {{
    font-size: 28px;
    background: linear-gradient(90deg, #f7971e, #ffd200);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .page-header .subtitle {{ color: #aaa; font-size: 14px; }}
  .stats-bar {{
    display: flex; gap: 12px; flex-wrap: wrap;
    justify-content: center; margin-bottom: 24px;
  }}
  .stat-chip {{
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 20px; padding: 8px 18px;
    font-size: 14px; color: #ccc;
  }}
  .stat-chip b {{ color: #ffd200; }}
  .top-authors {{ text-align: center; margin-bottom: 24px; }}
  .top-authors .label {{ font-size: 13px; color: #888; margin-bottom: 6px; }}
  .tag {{
    display: inline-block; background: rgba(247,151,30,0.15);
    color: #f7971e; border-radius: 12px; padding: 3px 10px;
    font-size: 12px; margin: 2px 3px;
  }}
  .tweet-card {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px; padding: 16px 20px;
    margin-bottom: 14px;
    transition: transform 0.15s, box-shadow 0.15s;
  }}
  .tweet-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 30px rgba(0,0,0,0.3);
    border-color: rgba(255,255,255,0.15);
  }}
  .tweet-header {{
    display: flex; align-items: center; gap: 8px;
    flex-wrap: wrap; margin-bottom: 10px;
  }}
  .rank {{ font-weight: 700; font-size: 16px; color: #ffd200; min-width: 36px; }}
  .score {{
    color: #fff; font-size: 12px; font-weight: 600;
    padding: 2px 10px; border-radius: 10px;
  }}
  .author {{
    color: #1d9bf0; text-decoration: none;
    font-weight: 600; font-size: 14px;
  }}
  .author:hover {{ text-decoration: underline; }}
  .name {{ color: #999; font-size: 13px; }}
  .time {{ color: #666; font-size: 12px; margin-left: auto; }}
  .tweet-body {{
    font-size: 15px; line-height: 1.7; color: #ddd;
    margin-bottom: 12px; word-break: break-word;
  }}
  .tweet-body a {{ color: #1d9bf0; text-decoration: none; }}
  .tweet-body a:hover {{ text-decoration: underline; }}
  .tweet-metrics {{
    display: flex; gap: 16px; align-items: center;
    font-size: 13px; color: #888;
  }}
  .link-btn {{
    margin-left: auto; color: #1d9bf0; text-decoration: none;
    font-size: 13px; padding: 3px 12px;
    border: 1px solid rgba(29,155,240,0.3); border-radius: 14px;
    transition: all 0.15s;
  }}
  .link-btn:hover {{
    background: rgba(29,155,240,0.15); border-color: #1d9bf0;
  }}
  .legend {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px; padding: 14px 20px;
    margin-bottom: 24px; font-size: 13px;
    color: #999; line-height: 1.8;
  }}
  .legend b {{ color: #ccc; }}
  .footer {{ text-align: center; padding: 30px; color: #555; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
  <div class="page-header">
    <h1>📊 {esc(title)}</h1>
    <div class="subtitle">基于互动指标 + 内容质量综合评分 · {now} 生成</div>
  </div>

  <div class="stats-bar">
    <div class="stat-chip">📝 总推文 <b>{total}</b></div>
    <div class="stat-chip">🔥 高质量 (≥50分) <b>{high_quality}</b></div>
    <div class="stat-chip">👤 独立作者 <b>{unique_authors}</b></div>
  </div>

  <div class="top-authors">
    <div class="label">🏆 Top 30 中最活跃作者</div>
    {top_authors_html}
  </div>

  <div class="legend">
    <b>评分规则：</b>
    ❤️ Like ×3 + 🔁 RT ×5 + 💬 Reply ×2 + 🔖 Bookmark ×8 +
    内容长度加分 + 技术关键词加分 + 结构化内容加分 + 资源链接加分<br/>
    <b>等级：</b>
    <span style="color:#e74c3c">🔥🔥🔥 ≥100</span> ·
    <span style="color:#e67e22">🔥🔥 ≥50</span> ·
    <span style="color:#f39c12">🔥 ≥20</span> ·
    <span style="color:#95a5a6">· &lt;20</span>
  </div>

  {"".join(rows_html)}

  <div class="footer">
    X Search Aggregator · Usefulness Ranking<br/>
    Generated at {now}
  </div>
</div>
</body>
</html>"""


# ── 主入口 ──

def main() -> None:
    args = parse_args()

    # 解析输入路径
    input_path = Path(args.input).expanduser().resolve()
    if input_path.is_dir():
        json_path = input_path / "results.json"
        out_dir = input_path
    else:
        json_path = input_path
        out_dir = input_path.parent

    if not json_path.exists():
        print(f"错误: 找不到文件 {json_path}")
        raise SystemExit(1)

    items = json.loads(json_path.read_text("utf-8"))
    if not items:
        print("错误: results.json 为空")
        raise SystemExit(1)

    print(f"读取 {len(items)} 条推文: {json_path}")

    # 评分 & 排序
    for item in items:
        item["_score"] = usefulness_score(item)
    items.sort(key=lambda x: x["_score"], reverse=True)

    # 页面标题
    title = args.title or f"推文有用程度排名 ({len(items)} 条)"

    # 输出路径
    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = out_dir / "usefulness_ranking.html"

    # 生成 HTML
    html_content = build_ranking_html(items, title)
    out_path.write_text(html_content, encoding="utf-8")

    # 同时保存带评分的 JSON
    ranking_json_path = out_dir / "usefulness_ranking.json"
    ranking_data = [
        {
            "rank": i + 1,
            "score": item["_score"],
            "tweet_id": item.get("tweet_id", ""),
            "url": item.get("url", ""),
            "user_handle": item.get("user_handle", ""),
            "user_name": item.get("user_name", ""),
            "posted_at": item.get("posted_at", ""),
            "text": item.get("text", ""),
            "like_count": item.get("like_count", 0),
            "retweet_count": item.get("retweet_count", 0),
            "reply_count": item.get("reply_count", 0),
            "bookmark_count": item.get("bookmark_count", 0),
            "view_count": item.get("view_count", 0),
        }
        for i, item in enumerate(items)
    ]
    ranking_json_path.write_text(
        json.dumps(ranking_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 打印结果
    high_quality = sum(1 for i in items if i["_score"] >= 50)
    print(f"\n✅ 已生成排名页面: {out_path}")
    print(f"   排名JSON: {ranking_json_path}")
    print(f"   总推文: {len(items)}")
    print(f"   高质量 (≥50分): {high_quality}")
    print(f"\n   Top 5:")
    for i, item in enumerate(items[:5], 1):
        print(
            f"   {i}. [{item['_score']:.0f}分] @{item.get('user_handle', '')} "
            f"- {(item.get('text', ''))[:60]}..."
        )


if __name__ == "__main__":
    main()
