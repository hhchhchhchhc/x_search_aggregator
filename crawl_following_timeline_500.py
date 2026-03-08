#!/usr/bin/env python3
"""获取个人账号关注的所有人的最新500条动态。

原理：加载 X.com 首页 Home Timeline（"Following"标签页），
该页面展示当前登录用户所关注的所有账号的最新动态，
通过持续滚动收集前500条推文并输出。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from playwright.sync_api import Page, sync_playwright, TimeoutError

from html_report import write_html_article
from search_x import (
    HASHTAG_RE,
    MENTION_RE,
    TWEET_SELECTORS,
    FEED_SELECTORS,
    END_MARKER_SELECTORS,
    create_context,
    extract_tweet,
    get_cards,
    get_last_visible_anchor,
    has_end_marker,
    parse_count,
    scroll_feed,
    to_dt,
    validate_auth_state,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="获取个人账号关注的所有人的最新500条动态",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python crawl_following_timeline_500.py --state auth_state.json
  python crawl_following_timeline_500.py --state auth_state.json --headless
        """,
    )
    parser.add_argument("--state", default="auth_state_cookie.json", help="Playwright存储状态路径")
    parser.add_argument("--headless", action="store_true", help="无头模式运行浏览器")
    parser.add_argument("--out-dir", default="output", help="输出基础目录")
    parser.add_argument("--max-scrolls", type=int, default=300, help="最大滚动轮数")
    parser.add_argument("--no-new-stop", type=int, default=12, help="连续N轮无新推文后停止")
    parser.add_argument("--scroll-pause", type=int, default=2000, help="滚动间隔（毫秒）")
    parser.add_argument("--skip-fulltext", action="store_true", help="跳过第二阶段全文补全")
    parser.add_argument("--fulltext-delay-ms", type=int, default=1200, help="打开推文详情页后的等待毫秒数")
    parser.add_argument("--fulltext-checkpoint-every", type=int, default=10, help="每补全N条写一次检查点")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# safe_name helper
# ---------------------------------------------------------------------------

def safe_name(text: str) -> str:
    text = re.sub(r"\s+", "_", str(text).strip())
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "", text)
    return text[:80] or "following"


# ---------------------------------------------------------------------------
# Switch to "Following" tab on home page
# ---------------------------------------------------------------------------

def switch_to_following_tab(page: Page) -> bool:
    """在首页点击"Following"标签页（关注），确保展示关注者动态而非推荐。"""

    # 方法1: 通过tab角色精准定位
    try:
        tabs = page.query_selector_all('a[role="tab"], div[role="tab"], span[role="tab"]')
        for tab in tabs:
            text = (tab.inner_text() or "").strip().lower()
            if text in ("following", "关注"):
                tab.click()
                page.wait_for_timeout(3000)
                print("已切换到 Following（关注）标签页")
                return True
    except Exception:
        pass

    # 方法2: 通过导航标签精准选择器
    selectors = [
        'a[href="/home"][role="tab"]:has-text("Following")',
        'a[href="/home"][role="tab"]:has-text("关注")',
        'div[role="presentation"] a:has-text("Following")',
        'div[role="presentation"] a:has-text("关注")',
        'nav a:has-text("Following")',
        'nav a:has-text("关注")',
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_timeout(3000)
                print("已切换到 Following（关注）标签页（通过选择器）")
                return True
        except Exception:
            continue

    # 方法3: 通过 evaluate JS 寻找并点击
    try:
        clicked = page.evaluate("""() => {
            const tabs = document.querySelectorAll('a[role="tab"], div[role="tablist"] a');
            for (const tab of tabs) {
                const text = (tab.textContent || '').trim().toLowerCase();
                if (text === 'following' || text === '关注') {
                    tab.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked:
            page.wait_for_timeout(3000)
            print("已切换到 Following（关注）标签页（通过JS）")
            return True
    except Exception:
        pass

    print("警告: 无法切换到Following标签页，将使用当前页面（可能包含推荐内容）")
    return False


# ---------------------------------------------------------------------------
# Collect tweets from home timeline
# ---------------------------------------------------------------------------

def collect_following_tweets(
    page: Page,
    max_items: int,
    max_scrolls: int,
    no_new_stop: int,
    scroll_pause: int,
    checkpoint_cb: Optional[Callable[[List[Dict], int, int], None]] = None,
) -> List[Dict]:
    """从首页时间线收集推文。"""
    seen: Dict[str, Dict] = {}
    seen_ids: Set[str] = set()
    no_new_rounds = 0
    anchor_stall_rounds = 0
    last_anchor = ""

    # 初始等待页面加载
    time.sleep(2)

    for idx in range(max_scrolls):
        cards = get_cards(page)
        new_count = 0

        for card in cards:
            try:
                item = extract_tweet(card)
                if not item:
                    continue
                tid = item["tweet_id"]
                if tid in seen_ids:
                    continue

                seen[tid] = item
                seen_ids.add(tid)
                new_count += 1

                if len(seen) >= max_items:
                    if checkpoint_cb:
                        checkpoint_cb(list(seen.values()), idx, new_count)
                    print(f"已达到目标条数: {max_items}")
                    return list(seen.values())
            except Exception as e:
                continue

        print(f"滚动 {idx + 1}/{max_scrolls}: +{new_count} 条新推文, 共 {len(seen)} 条")
        if checkpoint_cb:
            checkpoint_cb(list(seen.values()), idx, new_count)

        # 检查是否因无新内容而应停止
        if new_count == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0

        current_anchor = get_last_visible_anchor(page)
        if current_anchor and current_anchor == last_anchor:
            anchor_stall_rounds += 1
        elif not current_anchor and new_count == 0:
            anchor_stall_rounds += 1
        else:
            anchor_stall_rounds = 0
        last_anchor = current_anchor or last_anchor

        if has_end_marker(page) and new_count == 0:
            print("检测到末尾标记，停止滚动。")
            break

        if no_new_rounds >= min(no_new_stop, 6) and len(cards) == 0:
            print(f"连续 {no_new_rounds} 轮未检测到推文卡片，停止滚动。")
            break

        if no_new_rounds >= no_new_stop and anchor_stall_rounds >= 3:
            print(f"连续 {no_new_rounds} 轮无新推文且锚点停滞 {anchor_stall_rounds} 轮，停止滚动。")
            break

        scroll_feed(page, idx)
        pause_ms = scroll_pause if new_count > 0 else int(scroll_pause * 1.35)
        page.wait_for_timeout(pause_ms)

    return list(seen.values())


def checkpoint_following_outputs(run_dir: Path, items: List[Dict]) -> None:
    summary = summarize_following_timeline(items)
    (run_dir / "results.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(run_dir / "results.csv", items)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary_md(run_dir / "summary.md", summary)
    write_summary_html(run_dir / "summary.html", summary, items)
    write_html_article(run_dir / "article.html", "关注者最新动态", items)


def make_following_checkpoint_callback(
    run_dir: Path,
    every_n_scrolls: int = 5,
) -> Callable[[List[Dict], int, int], None]:
    last_saved = {"scroll": 0}

    def checkpoint(items: List[Dict], scroll_idx: int, new_count: int) -> None:
        if not items:
            return
        should_save = (
            scroll_idx == 0
            or new_count > 0 and (scroll_idx + 1 - last_saved["scroll"]) >= every_n_scrolls
        )
        if not should_save:
            return
        checkpoint_following_outputs(run_dir, items)
        last_saved["scroll"] = scroll_idx + 1
        print(f"Checkpoint saved: {run_dir / 'results.json'}")

    return checkpoint


# ---------------------------------------------------------------------------
# Summary & output helpers
# ---------------------------------------------------------------------------

def summarize_following_timeline(items: List[Dict]) -> Dict:
    """生成关注者动态摘要。"""
    hashtags: Counter = Counter()
    mentions: Counter = Counter()
    user_counts: Counter = Counter()

    for it in items:
        txt = it.get("text") or ""
        hashtags.update([h.lower() for h in HASHTAG_RE.findall(txt)])
        mentions.update([m.lower() for m in MENTION_RE.findall(txt)])
        handle = it.get("user_handle", "")
        if handle:
            user_counts[handle] += 1

    times = [to_dt(i.get("posted_at")) for i in items]
    times = [t for t in times if t is not None]
    times.sort()

    top_liked = sorted(items, key=lambda x: x.get("like_count", 0), reverse=True)[:20]

    return {
        "type": "following_timeline",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_collected": len(items),
        "unique_authors": len(user_counts),
        "time_range": {
            "from": times[0].isoformat() if times else None,
            "to": times[-1].isoformat() if times else None,
        },
        "most_active_authors": [
            {"handle": h, "tweet_count": c} for h, c in user_counts.most_common(30)
        ],
        "top_hashtags": [{"tag": t, "count": c} for t, c in hashtags.most_common(20)],
        "top_mentions": [{"username": u, "count": c} for u, c in mentions.most_common(20)],
        "top_liked": [
            {
                "tweet_id": i["tweet_id"],
                "url": i["url"],
                "user_handle": i.get("user_handle", ""),
                "like_count": i.get("like_count", 0),
                "retweet_count": i.get("retweet_count", 0),
                "reply_count": i.get("reply_count", 0),
                "text": (i.get("text") or "")[:240],
            }
            for i in top_liked
        ],
    }


def write_csv(path: Path, items: List[Dict]) -> None:
    fields = [
        "tweet_id",
        "url",
        "user_name",
        "user_handle",
        "posted_at",
        "card_text",
        "text",
        "full_text",
        "full_text_status",
        "reply_count",
        "retweet_count",
        "like_count",
        "bookmark_count",
        "view_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(items)


def write_summary_md(path: Path, summary: Dict) -> None:
    lines = [
        "# 关注者动态摘要 (Following Timeline)",
        "",
        f"- 生成时间 (UTC): {summary['generated_at']}",
        f"- 总收集推文数: {summary['total_collected']}",
        f"- 独立作者数: {summary['unique_authors']}",
        f"- 时间范围: {summary['time_range']['from']} ~ {summary['time_range']['to']}",
        "",
        "## 最活跃的关注者",
    ]
    for a in summary["most_active_authors"]:
        lines.append(f"- @{a['handle']}: {a['tweet_count']}条")

    lines.append("")
    lines.append("## 热门标签")
    for t in summary["top_hashtags"]:
        lines.append(f"- #{t['tag']}: {t['count']}")

    lines.append("")
    lines.append("## 热门提及")
    for u in summary["top_mentions"]:
        lines.append(f"- @{u['username']}: {u['count']}")

    lines.append("")
    lines.append("## 最热门推文")
    for i, row in enumerate(summary["top_liked"], start=1):
        lines.append(
            f"{i}. @{row['user_handle']} | likes={row['like_count']} | {row['url']}"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_html(path: Path, summary: Dict, items: List[Dict]) -> None:
    """生成关注者动态HTML报告。"""
    author_rows = "".join(
        f"<tr><td><a href='https://x.com/{a['handle']}' target='_blank'>@{a['handle']}</a></td><td>{a['tweet_count']}</td></tr>"
        for a in summary["most_active_authors"][:30]
    )
    tag_rows = "".join(
        f"<tr><td>#{t['tag']}</td><td>{t['count']}</td></tr>"
        for t in summary["top_hashtags"][:20]
    )
    top_rows = "".join(
        f"""<tr>
            <td><a href='{r['url']}' target='_blank'>@{r['user_handle']}</a></td>
            <td>{r['like_count']}</td>
            <td>{r['retweet_count']}</td>
            <td>{r['reply_count']}</td>
            <td>{(r.get('text') or '').replace('<','&lt;').replace('>','&gt;')[:120]}</td>
        </tr>"""
        for r in summary["top_liked"][:20]
    )

    html = f"""<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>关注者动态摘要</title>
  <style>
    body {{ font-family: "IBM Plex Sans", "Noto Sans SC", sans-serif; margin: 0; background: #f7f6f3; color: #1d1d1d; }}
    .wrap {{ max-width: 1060px; margin: 0 auto; padding: 24px; }}
    .card {{ background: #fff; border: 1px solid #ddd5c8; border-radius: 12px; padding: 16px 20px; margin-bottom: 16px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .meta {{ color: #666; font-size: 14px; margin-bottom: 6px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #ece7df; padding: 8px; text-align: left; }}
    th {{ background: #f3efe8; }}
    a {{ color: #1d9bf0; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="card">
      <h1>关注者动态摘要 (Following Timeline)</h1>
      <p class="meta">生成时间 (UTC): {summary['generated_at']}</p>
      <p class="meta">总收集推文数: {summary['total_collected']} | 独立作者数: {summary['unique_authors']}</p>
      <p class="meta">时间范围: {summary['time_range']['from']} ~ {summary['time_range']['to']}</p>
    </section>

    <section class="card">
      <h2>最活跃的关注者</h2>
      <table>
        <thead><tr><th>用户</th><th>推文数</th></tr></thead>
        <tbody>{author_rows}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>热门标签</h2>
      <table>
        <thead><tr><th>标签</th><th>次数</th></tr></thead>
        <tbody>{tag_rows}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>最热门推文 Top 20</h2>
      <table>
        <thead><tr><th>用户</th><th>Like</th><th>RT</th><th>Reply</th><th>内容</th></tr></thead>
        <tbody>{top_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""
    path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    max_items = 500  # 固定收集500条

    out_base = Path(args.out_dir).expanduser().resolve()
    run_dir = out_base / f"following_timeline_500_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"运行目录: {run_dir}")

    print("=" * 60)
    print("获取个人账号关注的所有人的最新500条动态")
    print(f"目标: 收集前 {max_items} 条最新内容")
    print("=" * 60)

    with sync_playwright() as p:
        context = create_context(p, args.state, args.headless)
        page = context.new_page()

        # 先打开首页（增大超时以适应慢网络）
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=120000)
        except Exception as e:
            print(f"警告: 页面加载超时或出错({e})，尝试继续...")
        page.wait_for_timeout(3000)

        # 验证认证状态
        if not validate_auth_state(page):
            print("错误: 检测到认证问题。请使用 login_x.py 刷新登录状态。")
            context.close()
            sys.exit(1)

        # 先等待首页初始加载
        loaded_initial = False
        for sel in TWEET_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=10000)
                loaded_initial = True
                break
            except TimeoutError:
                continue

        if not loaded_initial:
            print("警告: 首页初始加载未检测到推文，等待更久...")
            page.wait_for_timeout(5000)

        # 切换到 "Following"（关注）标签页
        switch_to_following_tab(page)

        # 等待切换后推文加载
        loaded = False
        for sel in TWEET_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=10000)
                loaded = True
                break
            except TimeoutError:
                continue

        if not loaded:
            # 重试：刷新页面再切换
            print("警告: 未检测到推文加载，尝试刷新后重试...")
            try:
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=120000)
            except Exception:
                pass
            page.wait_for_timeout(5000)
            switch_to_following_tab(page)
            page.wait_for_timeout(3000)

        # 收集推文
        items = collect_following_tweets(
            page=page,
            max_items=max_items,
            max_scrolls=args.max_scrolls,
            no_new_stop=args.no_new_stop,
            scroll_pause=args.scroll_pause,
            checkpoint_cb=make_following_checkpoint_callback(run_dir),
        )
        context.close()

    if not items:
        print("警告: 未收集到任何推文。请检查您的认证和网络连接。")
        sys.exit(1)

    # 按时间排序（最新在前），确保只保留前500条
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items_with_time = [(item, to_dt(item.get("posted_at"))) for item in items]
    items_with_time.sort(
        key=lambda x: x[1].replace(tzinfo=timezone.utc) if x[1] and x[1].tzinfo is None else (x[1] if x[1] else epoch),
        reverse=True,
    )
    items = [item for item, _ in items_with_time[:max_items]]

    print(f"\n成功收集 {len(items)} 条推文（目标: {max_items}条）")

    stage1_json_path = run_dir / "results_stage1.json"
    stage1_json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"阶段1结果已保存: {stage1_json_path}")

    if not args.skip_fulltext:
        from tweet_fulltext import hydrate_items_with_fulltext

        print("开始第二阶段：逐条补全推文全文...")
        with sync_playwright() as p:
            context = create_context(p, args.state, args.headless)
            items = hydrate_items_with_fulltext(
                context=context,
                items=items,
                run_dir=run_dir,
                checkpoint_every=args.fulltext_checkpoint_every,
                delay_ms=args.fulltext_delay_ms,
                logger=print,
            )
            context.close()

    # 生成摘要
    summary = summarize_following_timeline(items)

    # 保存文件
    json_path = run_dir / "results.json"
    csv_path = run_dir / "results.csv"
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    summary_html = run_dir / "summary.html"
    article_html = run_dir / "article.html"

    checkpoint_following_outputs(run_dir, items)

    print("=" * 60)
    print(f"完成！已收集 {len(items)} 条关注者动态。")
    print(f"独立作者数: {summary['unique_authors']}")
    print(f"结果JSON:   {json_path}")
    print(f"结果CSV:    {csv_path}")
    print(f"摘要JSON:   {summary_json}")
    print(f"摘要Markdown: {summary_md}")
    print(f"摘要HTML:   {summary_html}")
    print(f"文章HTML:   {article_html}")
    print("=" * 60)


if __name__ == "__main__":
    main()
