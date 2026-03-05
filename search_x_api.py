#!/usr/bin/env python3
"""Search X via official API v2 recent search endpoint and export results."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from html_report import write_html_article

API_URL = "https://api.x.com/2/tweets/search/recent"
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_\u4e00-\u9fff]+)")
MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Search X using official API")
    p.add_argument("--keyword", required=True, help="Search keyword")
    p.add_argument("--max-items", type=int, default=200, help="Max tweets to fetch")
    p.add_argument("--out-dir", default="output", help="Output base directory")
    p.add_argument("--lang", default="", help="Optional language filter, e.g. zh/en")
    p.add_argument("--bearer-token", default="", help="X API bearer token (or env X_BEARER_TOKEN)")
    p.add_argument("--max-retries", type=int, default=4, help="Max retries for transient errors")
    return p.parse_args()


def safe_name(text: str) -> str:
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "", text)
    return text[:60] or "keyword"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_query(keyword: str, lang: str) -> str:
    kw = re.sub(r"\s+", " ", keyword).strip()
    if lang:
        return f"({kw}) lang:{lang} -is:retweet"
    return f"({kw}) -is:retweet"


def parse_rate_limit_reset(err: HTTPError) -> Optional[int]:
    try:
        reset = err.headers.get("x-rate-limit-reset")
        if reset:
            return int(reset)
    except Exception:
        return None
    return None


def api_get(url: str, headers: Dict[str, str], max_retries: int) -> Dict:
    backoff = 2
    last_exc = None
    for _ in range(max_retries + 1):
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            last_exc = e
            if e.code == 429:
                reset_ts = parse_rate_limit_reset(e)
                if reset_ts:
                    sleep_s = max(1, reset_ts - int(time.time()) + 1)
                else:
                    sleep_s = backoff
                time.sleep(min(sleep_s, 120))
                backoff = min(backoff * 2, 30)
                continue
            if e.code >= 500:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            raise
        except URLError as e:
            last_exc = e
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
    raise RuntimeError(f"API request failed after retries: {last_exc}")


def collect(keyword: str, lang: str, max_items: int, token: str, max_retries: int) -> List[Dict]:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "x-search-aggregator/1.0"}
    query = build_query(keyword, lang)

    collected: List[Dict] = []
    seen_ids = set()
    next_token = None

    while len(collected) < max_items:
        page_size = min(100, max(10, max_items - len(collected)))
        params = {
            "query": query,
            "max_results": str(page_size),
            "tweet.fields": "created_at,author_id,public_metrics,lang",
            "expansions": "author_id",
            "user.fields": "username,name,verified",
        }
        if next_token:
            params["next_token"] = next_token

        url = f"{API_URL}?{urlencode(params)}"
        data = api_get(url, headers, max_retries=max_retries)

        users = {u.get("id", ""): u for u in data.get("includes", {}).get("users", [])}
        tweets = data.get("data", [])
        if not tweets:
            break

        for tw in tweets:
            tid = tw.get("id")
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            uid = tw.get("author_id", "")
            user = users.get(uid, {})
            m = tw.get("public_metrics", {})
            item = {
                "id": tid,
                "created_at": tw.get("created_at", ""),
                "text": tw.get("text", ""),
                "lang": tw.get("lang", ""),
                "author_id": uid,
                "username": user.get("username", ""),
                "name": user.get("name", ""),
                "verified": bool(user.get("verified", False)),
                "retweet_count": int(m.get("retweet_count", 0)),
                "reply_count": int(m.get("reply_count", 0)),
                "like_count": int(m.get("like_count", 0)),
                "quote_count": int(m.get("quote_count", 0)),
                "bookmark_count": int(m.get("bookmark_count", 0)),
                "impression_count": int(m.get("impression_count", 0)),
                "url": f"https://x.com/{user.get('username', 'i')}/status/{tid}",
            }
            collected.append(item)
            if len(collected) >= max_items:
                break

        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            break

    return collected


def write_outputs(keyword: str, out_base: Path, rows: List[Dict]) -> Path:
    run_dir = out_base / f"{safe_name(keyword)}_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "results.json"
    csv_path = run_dir / "results.csv"
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    article_html = run_dir / "article.html"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "id", "created_at", "username", "name", "verified", "lang", "text",
        "retweet_count", "reply_count", "like_count", "quote_count", "bookmark_count",
        "impression_count", "url",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    hashtags = Counter()
    mentions = Counter()
    for r in rows:
        txt = r.get("text", "")
        hashtags.update([x.lower() for x in HASHTAG_RE.findall(txt)])
        mentions.update([x.lower() for x in MENTION_RE.findall(txt)])

    top_by_like = sorted(rows, key=lambda x: x.get("like_count", 0), reverse=True)[:10]
    summary = {
        "generated_at_utc": now_utc_iso(),
        "total_items": len(rows),
        "top_hashtags": [{"tag": k, "count": v} for k, v in hashtags.most_common(20)],
        "top_mentions": [{"username": k, "count": v} for k, v in mentions.most_common(20)],
        "top_liked": [
            {
                "id": x.get("id"),
                "username": x.get("username"),
                "like_count": x.get("like_count", 0),
                "url": x.get("url"),
                "text": x.get("text", "")[:180],
            }
            for x in top_by_like
        ],
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# X API Search Summary: {keyword}",
        "",
        f"- Generated (UTC): {summary['generated_at_utc']}",
        f"- Total items: {summary['total_items']}",
        "",
        "## Top Hashtags",
    ]
    if summary["top_hashtags"]:
        md_lines.extend([f"- #{x['tag']}: {x['count']}" for x in summary["top_hashtags"][:10]])
    else:
        md_lines.append("- (none)")
    md_lines.append("")
    md_lines.append("## Top Mentions")
    if summary["top_mentions"]:
        md_lines.extend([f"- @{x['username']}: {x['count']}" for x in summary["top_mentions"][:10]])
    else:
        md_lines.append("- (none)")

    summary_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    write_html_article(article_html, keyword, rows)
    return run_dir


def main() -> None:
    args = parse_args()
    token = args.bearer_token or os.environ.get("X_BEARER_TOKEN", "")
    if not token:
        raise SystemExit("Missing bearer token. Pass --bearer-token or set env X_BEARER_TOKEN")

    print(f"Using X API recent search for keyword: {args.keyword}")
    rows = collect(
        keyword=args.keyword,
        lang=args.lang,
        max_items=args.max_items,
        token=token,
        max_retries=args.max_retries,
    )

    out_dir = Path(args.out_dir).expanduser().resolve()
    run_dir = write_outputs(args.keyword, out_dir, rows)

    print(f"Done. Collected {len(rows)} items.")
    print(f"Output: {run_dir}")


if __name__ == "__main__":
    main()
