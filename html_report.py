#!/usr/bin/env python3
"""Generate a deep summary HTML article from collected X posts."""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, List

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
ZH_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
STOPWORDS = {
    "我们", "你们", "他们", "这个", "那个", "以及", "如果", "因为", "所以", "就是", "一个", "没有",
    "可以", "已经", "还是", "不是", "自己", "真的", "这种", "这些", "那些", "进行", "相关", "需要",
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have", "has",
    "will", "about", "into", "your", "they", "their", "you", "our", "but", "not", "just", "all",
}

OPPORTUNITY_WORDS = {"机会", "增长", "红利", "突破", "效率", "创新", "升级", "潜力", "趋势", "爆发", "增量"}
RISK_WORDS = {"风险", "焦虑", "下滑", "失业", "泡沫", "崩", "诈骗", "困境", "压力", "寒冬", "割韭菜"}
ACTION_WORDS = {"建议", "应该", "必须", "可以", "需要", "先", "再", "步骤", "方法", "策略", "落地"}


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _norm_item(it: Dict) -> Dict:
    likes = _to_int(it.get("like_count"))
    rts = _to_int(it.get("retweet_count"))
    replies = _to_int(it.get("reply_count"))
    quotes = _to_int(it.get("quote_count"))
    bookmarks = _to_int(it.get("bookmark_count"))
    views = _to_int(it.get("view_count") or it.get("impression_count"))
    engagement = likes + 2 * rts + replies + quotes + bookmarks

    return {
        "id": str(it.get("tweet_id") or it.get("id") or ""),
        "author": it.get("user_handle") or it.get("username") or "unknown",
        "text": str(it.get("text") or "").strip(),
        "url": it.get("url") or "",
        "posted_at": it.get("posted_at") or it.get("created_at") or "",
        "likes": likes,
        "retweets": rts,
        "replies": replies,
        "quotes": quotes,
        "bookmarks": bookmarks,
        "views": views,
        "engagement": engagement,
    }


def _tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    tokens: List[str] = []
    tokens.extend([x.lower() for x in WORD_RE.findall(text)])
    tokens.extend([x for x in ZH_WORD_RE.findall(text)])
    return [t for t in tokens if t not in STOPWORDS and len(t) >= 2]


def _engagement_density(item: Dict) -> float:
    ln = max(1, len(item["text"]))
    return item["engagement"] / ln


def _safe_pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def analyze(items: List[Dict], keyword: str) -> Dict:
    rows = [_norm_item(i) for i in items if str(i.get("text") or "").strip()]
    total = len(rows)

    if total == 0:
        return {
            "keyword": keyword,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": 0,
            "top_terms": [],
            "topic_cards": [],
            "insights": ["未检测到可分析文本。"],
            "top_posts": [],
            "stats": {},
        }

    token_counter = Counter()
    doc_tokens: List[List[str]] = []
    for r in rows:
        toks = _tokenize(r["text"])
        doc_tokens.append(toks)
        token_counter.update(set(toks))

    top_terms = [t for t, _ in token_counter.most_common(12)]
    dominant_terms = top_terms[:6]

    topic_buckets: Dict[str, List[int]] = defaultdict(list)
    for i, toks in enumerate(doc_tokens):
        picked = None
        for term in dominant_terms:
            if term in toks:
                picked = term
                break
        topic_buckets[picked or "其他"].append(i)

    topic_cards = []
    for term, idxs in sorted(topic_buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]:
        sample = [rows[i] for i in idxs]
        sample_sorted = sorted(sample, key=lambda x: x["engagement"], reverse=True)
        avg_eng = sum(x["engagement"] for x in sample) / max(1, len(sample))
        rep = sample_sorted[0]
        topic_cards.append(
            {
                "title": term,
                "count": len(sample),
                "share": len(sample) / total,
                "avg_engagement": avg_eng,
                "representative": {
                    "author": rep["author"],
                    "text": rep["text"][:180],
                    "url": rep["url"],
                    "engagement": rep["engagement"],
                },
            }
        )

    engagements = [r["engagement"] for r in rows]
    top10 = sorted(rows, key=lambda x: x["engagement"], reverse=True)[:10]
    top10_share = sum(x["engagement"] for x in top10) / max(1, sum(engagements))

    opportunity_score = 0
    risk_score = 0
    action_score = 0
    for r in rows:
        txt = r["text"]
        opportunity_score += sum(1 for w in OPPORTUNITY_WORDS if w in txt)
        risk_score += sum(1 for w in RISK_WORDS if w in txt)
        action_score += sum(1 for w in ACTION_WORDS if w in txt)

    density_sorted = sorted(rows, key=_engagement_density, reverse=True)

    insights = [
        f"讨论重心集中在 {', '.join(dominant_terms[:3]) if dominant_terms else keyword}，头部主题对总样本覆盖率高，说明话题结构并非分散噪声，而是存在清晰主轴。",
        f"互动呈现明显头部集中：前 10 条内容贡献了 {_safe_pct(top10_share)} 的总互动，传播效率依赖少数高势能表达。",
        f"语义信号上，机会词({opportunity_score})、风险词({risk_score})、行动词({action_score}) 同时出现，表明讨论已从“观点表达”进入“策略落地”的过渡区间。",
    ]

    stats = {
        "total": total,
        "median_engagement": median(engagements),
        "avg_engagement": sum(engagements) / max(1, total),
        "top10_share": top10_share,
    }

    return {
        "keyword": keyword,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": total,
        "top_terms": top_terms,
        "topic_cards": topic_cards,
        "insights": insights,
        "top_posts": top10,
        "signal_posts": density_sorted[:6],
        "stats": stats,
    }


def _fmt_num(v: float) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def build_html(analysis: Dict) -> str:
    keyword = html.escape(analysis["keyword"])
    cards = []
    for c in analysis["topic_cards"]:
        rep = c["representative"]
        rep_text = html.escape(rep["text"])
        rep_url = html.escape(rep["url"] or "#")
        cards.append(
            f"""
            <article class=\"topic-card\"> 
              <h3>{html.escape(str(c['title']))}</h3>
              <p class=\"meta\">{c['count']} 条 | 占比 {_safe_pct(c['share'])} | 均值互动 {_fmt_num(c['avg_engagement'])}</p>
              <p class=\"quote\">“{rep_text}”</p>
              <a href=\"{rep_url}\" target=\"_blank\" rel=\"noreferrer\">代表内容</a>
            </article>
            """
        )

    insight_list = "".join(f"<li>{html.escape(x)}</li>" for x in analysis["insights"])

    top_posts = []
    for i, p in enumerate(analysis["top_posts"][:10], start=1):
        top_posts.append(
            f"""
            <tr>
              <td>{i}</td>
              <td>@{html.escape(p['author'])}</td>
              <td>{_fmt_num(p['engagement'])}</td>
              <td class=\"text-cell\">{html.escape(p['text'][:120])}</td>
              <td><a href=\"{html.escape(p['url'] or '#')}\" target=\"_blank\" rel=\"noreferrer\">链接</a></td>
            </tr>
            """
        )

    term_chips = "".join(f"<span>{html.escape(t)}</span>" for t in analysis["top_terms"][:12])
    stats = analysis["stats"]

    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{keyword} - 深度汇总文章</title>
  <style>
    :root {{
      --bg: #f5f3ef;
      --ink: #1d1d1b;
      --muted: #61605b;
      --accent: #0f766e;
      --card: #ffffffd8;
      --line: #d8d1c5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 110% -10%, #d9efe4 0%, transparent 70%),
        radial-gradient(1000px 520px at -10% 0%, #f7e8cf 0%, transparent 65%),
        var(--bg);
      line-height: 1.7;
    }}
    .wrap {{ max-width: 1080px; margin: 0 auto; padding: 36px 20px 60px; }}
    .hero h1 {{
      font-family: "Source Han Serif SC", "Noto Serif CJK SC", serif;
      font-size: clamp(2rem, 4vw, 3rem);
      margin: 0;
      letter-spacing: 0.02em;
    }}
    .hero p {{ color: var(--muted); margin-top: 10px; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 12px; margin: 24px 0; }}
    .stat {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px; }}
    .stat .k {{ color: var(--muted); font-size: 0.9rem; }}
    .stat .v {{ font-size: 1.4rem; font-weight: 650; margin-top: 6px; }}
    section {{ margin-top: 28px; }}
    h2 {{ font-size: 1.25rem; margin: 0 0 10px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chips span {{ background: #ece8df; border: 1px solid #d8d1c5; border-radius: 999px; padding: 4px 10px; font-size: 0.88rem; }}
    .topic-grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 12px; }}
    .topic-card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px; }}
    .topic-card h3 {{ margin: 0 0 6px; font-size: 1.05rem; }}
    .topic-card .meta {{ color: var(--muted); margin: 0 0 8px; font-size: 0.88rem; }}
    .topic-card .quote {{ margin: 0 0 10px; }}
    .topic-card a {{ color: var(--accent); text-decoration: none; }}
    .table-wrap {{ overflow: auto; background: var(--card); border: 1px solid var(--line); border-radius: 14px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 820px; }}
    th,td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ background: #f2eee7; font-weight: 650; }}
    td.text-cell {{ max-width: 420px; }}
    .foot {{ color: var(--muted); margin-top: 24px; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <main class=\"wrap\">
    <header class=\"hero\">
      <h1>「{keyword}」舆情深度汇总</h1>
      <p>生成时间（UTC）：{html.escape(analysis['generated_at'])}</p>
    </header>

    <section class=\"stats\">
      <div class=\"stat\"><div class=\"k\">样本量</div><div class=\"v\">{_fmt_num(stats.get('total',0))}</div></div>
      <div class=\"stat\"><div class=\"k\">平均互动</div><div class=\"v\">{_fmt_num(stats.get('avg_engagement',0))}</div></div>
      <div class=\"stat\"><div class=\"k\">中位互动</div><div class=\"v\">{_fmt_num(stats.get('median_engagement',0))}</div></div>
      <div class=\"stat\"><div class=\"k\">前10互动占比</div><div class=\"v\">{_safe_pct(stats.get('top10_share',0))}</div></div>
    </section>

    <section>
      <h2>核心判断</h2>
      <ul>{insight_list}</ul>
    </section>

    <section>
      <h2>高频议题词</h2>
      <div class=\"chips\">{term_chips}</div>
    </section>

    <section>
      <h2>议题结构拆解</h2>
      <div class=\"topic-grid">{''.join(cards)}</div>
    </section>

    <section>
      <h2>高互动内容榜（Top 10）</h2>
      <div class=\"table-wrap\">
        <table>
          <thead><tr><th>#</th><th>作者</th><th>互动</th><th>内容摘要</th><th>链接</th></tr></thead>
          <tbody>{''.join(top_posts)}</tbody>
        </table>
      </div>
    </section>

    <p class=\"foot\">本页面由本地脚本自动生成，用于信息聚合与研究，不构成投资/法律建议。</p>
  </main>
</body>
</html>
"""


def write_html_article(path: Path, keyword: str, items: List[Dict]) -> Path:
    analysis = analyze(items, keyword)
    html_doc = build_html(analysis)
    path.write_text(html_doc, encoding="utf-8")

    # Save machine-readable analysis alongside article.
    analysis_path = path.with_name("article_analysis.json")
    analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
