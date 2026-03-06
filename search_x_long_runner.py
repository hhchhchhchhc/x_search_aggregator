#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, json, re, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import quote
from playwright.sync_api import sync_playwright
from html_report import write_html_article

HASHTAG_RE = re.compile(r"#([A-Za-z0-9_\u4e00-\u9fff]+)")
MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--keyword", required=True)
    p.add_argument("--target", type=int, default=3000)
    p.add_argument("--state", default="auth_state_cookie.json")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--run-name", default="")
    return p.parse_args()


def parse_result(result: dict):
    legacy = result.get("legacy") or {}
    tid = result.get("rest_id") or legacy.get("id_str")
    text = legacy.get("full_text") or ""
    if not tid or not text:
        return None
    core = ((result.get("core") or {}).get("user_results") or {}).get("result") or {}
    uleg = core.get("legacy") or {}
    handle = uleg.get("screen_name") or ""
    name = uleg.get("name") or ""
    url = f"https://x.com/{handle}/status/{tid}" if handle else f"https://x.com/i/web/status/{tid}"
    v = (result.get("views") or {}).get("count")
    views = int(v) if isinstance(v, str) and v.isdigit() else (v if isinstance(v, int) else 0)
    return {
        "tweet_id": tid,
        "url": url,
        "user_name": name,
        "user_handle": handle,
        "posted_at": legacy.get("created_at", ""),
        "text": text,
        "reply_count": int(legacy.get("reply_count") or 0),
        "retweet_count": int(legacy.get("retweet_count") or 0),
        "like_count": int(legacy.get("favorite_count") or 0),
        "bookmark_count": int(legacy.get("bookmark_count") or 0),
        "view_count": views,
    }


def walk_collect(obj, out):
    if isinstance(obj, dict):
        tr = obj.get("tweet_results")
        if isinstance(tr, dict):
            r = tr.get("result")
            if isinstance(r, dict):
                item = parse_result(r)
                if item:
                    out.append(item)
        for v in obj.values():
            walk_collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_collect(v, out)


def write_outputs(run_dir: Path, keyword: str, target: int, rows: list[dict]):
    likes = [int(x.get("like_count", 0)) for x in rows]
    rts = [int(x.get("retweet_count", 0)) for x in rows]
    replies = [int(x.get("reply_count", 0)) for x in rows]
    views = [int(x.get("view_count", 0)) for x in rows if int(x.get("view_count", 0)) > 0]

    hashtags, mentions = Counter(), Counter()
    for r in rows:
        txt = r.get("text", "")
        hashtags.update([x.lower() for x in HASHTAG_RE.findall(txt)])
        mentions.update([x.lower() for x in MENTION_RE.findall(txt)])

    top = sorted(
        rows,
        key=lambda x: int(x.get("like_count", 0)) + 2 * int(x.get("retweet_count", 0)) + int(x.get("reply_count", 0)),
        reverse=True,
    )[:30]

    detail = {
        "keyword": keyword,
        "target": target,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_collected": len(rows),
        "completion_ratio": round(len(rows) / target, 4) if target else None,
        "engagement": {
            "avg_like": round(sum(likes) / len(rows), 3) if rows else 0,
            "median_like": median(likes) if likes else 0,
            "avg_retweet": round(sum(rts) / len(rows), 3) if rows else 0,
            "avg_reply": round(sum(replies) / len(rows), 3) if rows else 0,
            "avg_view": round(sum(views) / len(views), 3) if views else None,
        },
        "top_hashtags": [{"tag": k, "count": v} for k, v in hashtags.most_common(50)],
        "top_mentions": [{"username": k, "count": v} for k, v in mentions.most_common(50)],
        "top_engagement_tweets": top,
    }

    json_path = run_dir / "results.json"
    csv_path = run_dir / "results.csv"
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    article_html = run_dir / "article.html"
    detail_json = run_dir / "detailed_report.json"
    detail_md = run_dir / "detailed_report.md"
    detail_html = run_dir / "detailed_report.html"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "tweet_id",
                "url",
                "user_name",
                "user_handle",
                "posted_at",
                "text",
                "reply_count",
                "retweet_count",
                "like_count",
                "bookmark_count",
                "view_count",
            ],
        )
        w.writeheader()
        w.writerows(rows)

    summary_json.write_text(
        json.dumps(
            {
                "keyword": keyword,
                "target": target,
                "total_collected": len(rows),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_md.write_text(f"# Search Summary: {keyword}\n\n- Target: {target}\n- Collected: {len(rows)}\n", encoding="utf-8")
    write_html_article(article_html, keyword, rows)
    detail_json.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_md.write_text(
        "\n".join(
            [
                f"# Detailed Report: {keyword}",
                "",
                f"- Target: {target}",
                f"- Collected: {len(rows)}",
                f"- Completion ratio: {detail['completion_ratio']}",
                "",
                "## Top hashtags",
                *[f"- #{x['tag']}: {x['count']}" for x in detail["top_hashtags"][:30]],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    detail_html.write_text(
        f"<html><body><h1>{keyword} Detailed Report</h1><p>target={target}</p><p>collected={len(rows)}</p><p>completion={detail['completion_ratio']}</p></body></html>",
        encoding="utf-8",
    )


def main():
    args = parse_args()
    name = args.run_name or f"{args.keyword}_{int(time.time())}_network3000_runner"
    run_dir = Path(args.out_dir) / name
    run_dir.mkdir(parents=True, exist_ok=True)
    partial = run_dir / "results.partial.json"

    rows = []
    seen = set()
    if partial.exists():
        try:
            rows = json.loads(partial.read_text(encoding="utf-8"))
            for r in rows:
                tid = r.get("tweet_id")
                if tid:
                    seen.add(tid)
        except Exception:
            rows = []
            seen = set()

    req_count = 0
    blocked_until = 0
    last_resp_ts = time.time()

    def save_partial():
        partial.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    print(f"run_dir={run_dir}", flush=True)
    print(f"resume_count={len(rows)}", flush=True)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        c = b.new_context(storage_state=args.state)
        page = c.new_page()

        def on_response(resp):
            nonlocal req_count, blocked_until, last_resp_ts
            if "/SearchTimeline?" not in resp.url:
                return
            req_count += 1
            last_resp_ts = time.time()
            h = resp.headers
            status = resp.status
            remain = h.get("x-rate-limit-remaining", "?")
            reset = h.get("x-rate-limit-reset")

            if status == 429:
                try:
                    blocked_until = max(blocked_until, int(reset or 0))
                except Exception:
                    blocked_until = max(blocked_until, int(time.time()) + 60)
                print(f"req {req_count} 429 remain {remain} reset {reset} total {len(rows)}", flush=True)
                return

            try:
                data = resp.json()
            except Exception:
                print(f"req {req_count} status {status} parse_error total {len(rows)}", flush=True)
                return

            batch = []
            walk_collect(data, batch)
            new = 0
            for it in batch:
                tid = it["tweet_id"]
                if tid in seen:
                    continue
                seen.add(tid)
                rows.append(it)
                new += 1

            if new and len(rows) % 50 <= new:
                save_partial()
            print(f"req {req_count} status {status} remain {remain} batch {len(batch)} new {new} total {len(rows)}", flush=True)

        page.on("response", on_response)
        page.goto(f"https://x.com/search?q={quote(args.keyword, safe='')}&src=typed_query&f=live", wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(5000)

        loops = 0
        while len(rows) < args.target and loops < 100000:
            loops += 1
            now = int(time.time())
            if blocked_until > now:
                wait_s = min(60, blocked_until - now + 1)
                print(f"rate_limited wait {wait_s}s to {blocked_until}, total {len(rows)}", flush=True)
                page.wait_for_timeout(wait_s * 1000)
                continue

            page.keyboard.press("End")
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(1100)

            if time.time() - last_resp_ts > 90:
                print(f"no_response_90s reload total={len(rows)}", flush=True)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=120000)
                    page.wait_for_timeout(4000)
                except Exception:
                    pass
                last_resp_ts = time.time()

        b.close()

    rows = rows[: args.target]
    save_partial()
    write_outputs(run_dir, args.keyword, args.target, rows)
    print(f"final_count={len(rows)}", flush=True)


if __name__ == "__main__":
    main()
