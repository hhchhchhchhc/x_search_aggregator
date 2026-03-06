#!/usr/bin/env python3
"""搜索X.com关键词，返回前500条最新内容。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 导入search_x.py中的功能
from search_x import (
    create_context,
    validate_auth_state,
    wait_for_search_results,
    get_cards,
    handle_search_error_retry,
    collect_tweets,
    summarize,
    make_search_url,
    fallback_search_via_input,
    write_csv,
    write_summary_md,
    safe_name,
)
from html_report import write_html_article
import json
from datetime import datetime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="搜索X.com关键词，返回前500条最新内容",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python search_keyword_500.py --keyword "AI工具" --lang zh
  python search_keyword_500.py --keyword "python programming" --lang en
        """
    )
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--lang", default="", help="语言过滤，例如: zh/en")
    parser.add_argument("--state", default="auth_state_cookie.json", help="Playwright存储状态路径")
    parser.add_argument("--headless", action="store_true", help="无头模式运行浏览器")
    parser.add_argument("--out-dir", default="output", help="输出基础目录")
    parser.add_argument("--max-scrolls", type=int, default=200, help="最大滚动轮数")
    parser.add_argument("--no-new-stop", type=int, default=10, help="连续N轮无新推文后停止")
    parser.add_argument("--scroll-pause", type=int, default=2000, help="滚动间隔（毫秒）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    # 固定设置为500条最新内容
    max_items = 500
    sort = "Latest"  # 固定使用最新排序

    out_base = Path(args.out_dir).expanduser().resolve()
    run_dir = out_base / f"{safe_name(args.keyword)}_500_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    search_url = make_search_url(args.keyword, sort, args.lang)
    print(f"搜索关键词: {args.keyword}")
    print(f"搜索URL: {search_url}")
    print(f"目标: 收集前{max_items}条最新内容")
    print("=" * 60)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = create_context(p, args.state, args.headless)
        page = context.new_page()
        
        # 导航到搜索URL（增大超时以适应慢网络）
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=120000)
        except Exception as e:
            print(f"警告: 页面加载超时或出错({e})，尝试继续...")
        page.wait_for_timeout(3000)
        
        # 验证认证状态
        if not validate_auth_state(page):
            print("错误: 检测到认证问题。请使用 login_x.py 刷新登录状态。")
            context.close()
            sys.exit(1)
        
        # 等待搜索结果
        if not wait_for_search_results(page):
            print("警告: 搜索结果可能未正确加载，尝试使用输入框回退方法...")
            fallback_search_via_input(page, args.keyword, sort, args.lang)
            wait_for_search_results(page, timeout=15000)
        
        # 如果搜索时间线暂时出错，尝试重试按钮恢复
        if not get_cards(page):
            handle_search_error_retry(page, attempts=5)
        
        # 收集推文
        items = collect_tweets(
            page=page,
            max_items=max_items,
            max_scrolls=args.max_scrolls,
            no_new_stop=args.no_new_stop,
            scroll_pause=args.scroll_pause,
        )
        context.close()

    if not items:
        print("警告: 未收集到任何推文。请检查您的认证和网络连接。")
        sys.exit(1)

    # 确保只返回前500条（按时间排序，最新的在前）
    from datetime import timezone as tz
    from search_x import to_dt
    epoch = datetime(1970, 1, 1, tzinfo=tz.utc)
    items_with_time = [(item, to_dt(item.get("posted_at"))) for item in items]
    items_with_time.sort(
        key=lambda x: x[1].replace(tzinfo=tz.utc) if x[1] and x[1].tzinfo is None else (x[1] if x[1] else epoch),
        reverse=True,
    )
    items = [item for item, _ in items_with_time[:max_items]]

    print(f"成功收集 {len(items)} 条推文（目标: {max_items}条）")

    # 生成摘要
    summary = summarize(items, args.keyword)

    # 保存结果
    json_path = run_dir / "results.json"
    csv_path = run_dir / "results.csv"
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    article_html = run_dir / "article.html"

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, items)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary_md(summary_md, summary)
    write_html_article(article_html, args.keyword, items)

    print("=" * 60)
    print(f"完成！已收集 {len(items)} 条推文。")
    print(f"结果JSON:   {json_path}")
    print(f"结果CSV:    {csv_path}")
    print(f"摘要JSON:   {summary_json}")
    print(f"摘要Markdown: {summary_md}")
    print(f"摘要文章:   {article_html}")
    print("=" * 60)


if __name__ == "__main__":
    main()
