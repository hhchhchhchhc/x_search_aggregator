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
import math
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

EFFICIENCY_TERMS = [
    "agent", "agents", "workflow", "自动化", "automation", "效率", "productivity",
    "工具", "tool", "tools", "mcp", "prompt", "prompts", "api", "sdk",
    "cursor", "codex", "claude", "copilot", "代码", "coding", "开发",
    "deploy", "deployment", "review", "测试", "testing", "cli", "插件", "plugin",
]

AI_RESEARCH_TERMS = [
    "llm", "llms", "agi", "transformer", "transformers", "模型", "model", "models",
    "训练", "training", "推理", "inference", "reasoning", "alignment", "对齐",
    "eval", "evaluation", "benchmark", "benchmarks", "agentic", "planning",
    "memory", "retrieval", "embedding", "微调", "finetuning", "fine-tuning",
    "distillation", "multimodal", "diffusion", "rl", "rlhf", "policy", "research",
    "paper", "papers", "openai", "anthropic", "deepmind",
]


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

def _days_since(posted_at: str) -> float | None:
    if not posted_at:
        return None
    try:
        return max(
            0.0,
            (datetime.now(datetime.now().astimezone().tzinfo) - datetime.fromisoformat(posted_at.replace("Z", "+00:00")).astimezone()).total_seconds() / 86400,
        )
    except Exception:
        return None


def usefulness_breakdown(item: Dict) -> Dict[str, float]:
    """综合评分拆解：互动归一化 + 内容质量 + 轻量时效加分。"""
    likes = int(item.get("like_count", 0))
    rts = int(item.get("retweet_count", 0))
    reps = int(item.get("reply_count", 0))
    bmarks = int(item.get("bookmark_count", 0))
    views = int(item.get("view_count", 0))
    text = (item.get("text") or "").strip()
    text_len = len(text)

    # 互动分做对数归一化，避免超大号完全碾压中腰部高质量内容
    engagement_raw = likes * 3 + rts * 5 + reps * 2 + bmarks * 8
    engagement = min(math.log1p(max(0, engagement_raw)) * 12, 65)

    # 浏览量加分（对数归一化，上限 18）
    view_bonus = min(math.log1p(max(0, views)) * 1.5, 18) if views > 0 else 0

    # 内容长度加分（上限 30）
    length_bonus = min(text_len / 28, 16)

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

    # 轻量时效加分，鼓励最近 72 小时内容优先
    recency_bonus = 0.0
    days_old = _days_since(str(item.get("posted_at", "")))
    if days_old is not None:
        recency_bonus = max(0.0, 12 - min(days_old, 3) * 4)

    total = engagement + view_bonus + length_bonus + quality_bonus + recency_bonus - spam_penalty
    return {
        "engagement": round(engagement, 2),
        "views": round(view_bonus, 2),
        "length": round(length_bonus, 2),
        "quality": round(quality_bonus, 2),
        "recency": round(recency_bonus, 2),
        "penalty": round(spam_penalty, 2),
        "total": round(total, 2),
    }


def usefulness_score(item: Dict) -> float:
    return usefulness_breakdown(item)["total"]


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


def matched_terms(text: str, terms: List[str]) -> List[str]:
    lower = text.lower()
    hits: List[str] = []
    for term in terms:
        lowered = term.lower()
        if re.search(r"[a-zA-Z]", lowered):
            if re.search(rf"(?<![a-z0-9_]){re.escape(lowered)}(?![a-z0-9_])", lower):
                hits.append(term)
        else:
            if lowered in lower:
                hits.append(term)
    return hits


def build_efficiency_reason(text: str, terms: List[str]) -> str:
    parts = []
    lower = text.lower()
    if any(term in terms for term in ["agent", "agents", "workflow", "自动化", "automation"]):
        parts.append("这条内容直接涉及 agent、自动化或工作流组织，对提升日常信息处理和开发效率帮助最大。")
    if any(term in terms for term in ["工具", "tool", "tools", "plugin", "插件", "cli", "mcp", "api", "sdk"]):
        parts.append("它更偏工具或接口落地，适合直接拿来接入现有工作流，而不只是停留在概念层。")
    if any(term in terms for term in ["cursor", "codex", "claude", "copilot", "代码", "coding", "开发"]):
        parts.append("内容和 AI 编程助手或开发提速直接相关，通常最容易转化为可执行的提效动作。")
    if "教程" in lower or "方法" in lower or "best practice" in lower or "最佳实践" in lower:
        parts.append("同时它带有较强的方法论属性，适合直接照着试、照着改。")
    if not parts:
        parts.append("这条内容和效率提升或工程工具链有明显关联，值得优先阅读。")
    return "".join(parts)


def build_research_reason(text: str, terms: List[str]) -> str:
    parts = []
    lower = text.lower()
    if any(term in terms for term in ["llm", "llms", "transformer", "transformers", "模型", "model", "models"]):
        parts.append("这条内容更接近模型层讨论，能帮助你理解当前方法的能力边界和结构特点。")
    if any(term in terms for term in ["训练", "training", "推理", "inference", "reasoning", "eval", "evaluation", "benchmark", "benchmarks"]):
        parts.append("它覆盖训练、推理或评测问题，对研究思路、实验设计和阅读论文都更有启发。")
    if any(term in terms for term in ["agentic", "planning", "memory", "retrieval", "embedding"]):
        parts.append("内容涉及 agent 系统能力扩展，例如规划、记忆或检索，这类主题对 AI 系统研究很关键。")
    if any(term in terms for term in ["alignment", "对齐", "rl", "rlhf", "policy"]):
        parts.append("如果你关注对齐、强化学习或策略优化，这条内容的研究相关性会更强。")
    if "paper" in lower or "论文" in lower or "research" in lower or "openai" in lower or "anthropic" in lower:
        parts.append("来源或表述本身带有明显研究导向，适合继续深挖成论文线索或实验想法。")
    if not parts:
        parts.append("这条内容和 AI 方法、系统能力或研究方向相关，值得作为研究线索保留。")
    return "".join(parts)


def curated_highlights(items: List[Dict]) -> Dict[str, List[Dict]]:
    efficiency: List[Dict] = []
    research: List[Dict] = []

    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            continue

        eff_terms = matched_terms(text, EFFICIENCY_TERMS)
        if eff_terms:
            efficiency.append(
                {
                    "item": item,
                    "matched_terms": eff_terms[:8],
                    "reason": build_efficiency_reason(text, eff_terms[:8]),
                }
            )

        res_terms = matched_terms(text, AI_RESEARCH_TERMS)
        if res_terms:
            research.append(
                {
                    "item": item,
                    "matched_terms": res_terms[:8],
                    "reason": build_research_reason(text, res_terms[:8]),
                }
            )

    efficiency.sort(key=lambda row: row["item"].get("_score", 0), reverse=True)
    research.sort(key=lambda row: row["item"].get("_score", 0), reverse=True)
    return {
        "efficiency": efficiency[:6],
        "research": research[:6],
    }


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
    highlights = curated_highlights(items)

    def build_highlight_cards(rows: List[Dict]) -> str:
        if not rows:
            return '<div class="highlight-empty">当前结果里没有明显匹配的条目。</div>'
        cards = []
        for row in rows:
            item = row["item"]
            url = esc(item.get("url", ""))
            handle = esc(item.get("user_handle", ""))
            score = float(item.get("_score", 0))
            text_preview = esc((item.get("text", "") or "").strip()[:220])
            terms_html = " ".join(f'<span class="reason-chip">{esc(term)}</span>' for term in row["matched_terms"])
            cards.append(f"""
      <article class="highlight-item">
        <div class="highlight-head">
          <a href="{url}" target="_blank" rel="noopener">@{handle or '-'}</a>
          <span class="highlight-score">{score:.0f} 分</span>
        </div>
        <div class="highlight-text">{text_preview}</div>
        <div class="highlight-reason">{esc(row["reason"])}</div>
        <div class="highlight-tags">{terms_html}</div>
      </article>""")
        return "".join(cards)

    efficiency_html = build_highlight_cards(highlights["efficiency"])
    research_html = build_highlight_cards(highlights["research"])

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

        breakdown = item.get("_score_breakdown", {})
        reason_html = " ".join(
            f'<span class="reason-chip">{label} {breakdown.get(key, 0):.0f}</span>'
            for key, label in [
                ("engagement", "互动"),
                ("quality", "内容"),
                ("recency", "时效"),
            ]
        )

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
      <div class="score-reasons">{reason_html}</div>
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
    background:
      radial-gradient(circle at top left, rgba(255, 196, 92, 0.22), transparent 28%),
      radial-gradient(circle at top right, rgba(70, 149, 255, 0.2), transparent 26%),
      linear-gradient(180deg, #0b1220, #131b2f 52%, #0f1726);
    color: #e6edf7;
    min-height: 100vh;
  }}
  .container {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 48px; }}
  .page-header {{
    text-align: center; padding: 46px 22px 34px;
    background: rgba(10,16,28,0.62); border-radius: 22px;
    margin-bottom: 24px; backdrop-filter: blur(14px);
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 20px 60px rgba(0,0,0,0.22);
  }}
  .page-header h1 {{
    font-size: 32px;
    background: linear-gradient(90deg, #ffd166, #7cc6fe);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .page-header .subtitle {{ color: #9fb0c8; font-size: 14px; }}
  .stats-bar {{
    display: flex; gap: 12px; flex-wrap: wrap;
    justify-content: center; margin-bottom: 24px;
  }}
  .stat-chip {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 999px; padding: 9px 18px;
    font-size: 14px; color: #bfd0e8;
  }}
  .stat-chip b {{ color: #ffd166; }}
  .top-authors {{ text-align: center; margin-bottom: 24px; }}
  .top-authors .label {{ font-size: 13px; color: #888; margin-bottom: 6px; }}
  .highlight-grid {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .highlight-panel {{
    background: rgba(15, 23, 38, 0.72);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    padding: 16px 18px;
  }}
  .highlight-panel h3 {{
    margin-bottom: 12px;
    font-size: 18px;
    color: #ffd166;
  }}
  .highlight-item {{
    border-top: 1px solid rgba(255,255,255,0.08);
    padding-top: 12px;
    margin-top: 12px;
  }}
  .highlight-item:first-child {{
    border-top: 0;
    margin-top: 0;
    padding-top: 0;
  }}
  .highlight-head {{
    display: flex;
    justify-content: space-between;
    gap: 10px;
    align-items: center;
    margin-bottom: 8px;
  }}
  .highlight-head a {{
    color: #7cc6fe;
    text-decoration: none;
    font-weight: 700;
  }}
  .highlight-score {{
    color: #0b1220;
    background: #ffd166;
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 700;
    white-space: nowrap;
  }}
  .highlight-text {{
    color: #dce6f4;
    font-size: 14px;
    line-height: 1.7;
    margin-bottom: 8px;
    word-break: break-word;
  }}
  .highlight-reason {{
    color: #b7c6da;
    font-size: 13px;
    line-height: 1.75;
    margin-bottom: 8px;
  }}
  .highlight-tags {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .highlight-empty {{
    color: #94a3b8;
    font-size: 13px;
    line-height: 1.7;
  }}
  .tag {{
    display: inline-block; background: rgba(255, 209, 102, 0.12);
    color: #ffd166; border-radius: 999px; padding: 4px 10px;
    font-size: 12px; margin: 2px 3px;
  }}
  .tweet-card {{
    background: rgba(15, 23, 38, 0.78);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px; padding: 18px 22px;
    margin-bottom: 14px;
    transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
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
  .rank {{ font-weight: 700; font-size: 16px; color: #ffd166; min-width: 36px; }}
  .score {{
    color: #fff; font-size: 12px; font-weight: 600;
    padding: 2px 10px; border-radius: 10px;
  }}
  .author {{
    color: #1d9bf0; text-decoration: none;
    font-weight: 600; font-size: 14px;
  }}
  .author:hover {{ text-decoration: underline; }}
  .name {{ color: #91a0b5; font-size: 13px; }}
  .time {{ color: #718096; font-size: 12px; margin-left: auto; }}
  .tweet-body {{
    font-size: 15px; line-height: 1.72; color: #e8eef8;
    margin-bottom: 12px; word-break: break-word;
  }}
  .tweet-body a {{ color: #1d9bf0; text-decoration: none; }}
  .tweet-body a:hover {{ text-decoration: underline; }}
  .score-reasons {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
  .reason-chip {{
    font-size: 12px; color: #b7c6da; border-radius: 999px;
    padding: 4px 10px; background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
  }}
  .tweet-metrics {{
    display: flex; gap: 16px; align-items: center;
    flex-wrap: wrap; font-size: 13px; color: #94a3b8;
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
    background: rgba(15, 23, 38, 0.62);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px; padding: 16px 20px;
    margin-bottom: 24px; font-size: 13px;
    color: #a8b4c6; line-height: 1.8;
  }}
  .legend b {{ color: #d5dfec; }}
  .footer {{ text-align: center; padding: 30px; color: #66758b; font-size: 12px; }}
  @media (max-width: 820px) {{
    .highlight-grid {{ grid-template-columns: 1fr; }}
  }}
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
    互动分采用对数归一化，避免超大号纯靠体量霸榜；同时叠加内容长度、关键词、结构化、链接与时效加分。<br/>
    <b>等级：</b>
    <span style="color:#e74c3c">🔥🔥🔥 ≥100</span> ·
    <span style="color:#e67e22">🔥🔥 ≥50</span> ·
    <span style="color:#f39c12">🔥 ≥20</span> ·
    <span style="color:#95a5a6">· &lt;20</span>
  </div>

  <div class="highlight-grid">
    <section class="highlight-panel">
      <h3>超级提高效率最优帮助</h3>
      {efficiency_html}
    </section>
    <section class="highlight-panel">
      <h3>对 AI 研究最有启发</h3>
      {research_html}
    </section>
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
        item["_score_breakdown"] = usefulness_breakdown(item)
        item["_score"] = item["_score_breakdown"]["total"]
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
            "score_breakdown": item.get("_score_breakdown", {}),
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
