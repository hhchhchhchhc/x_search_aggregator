#!/usr/bin/env python3
"""Crawl accounts followed by a target user on X and generate detailed reports."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlencode

from playwright.sync_api import BrowserContext, sync_playwright

from search_x import create_context, validate_auth_state

USER_BY_SCREEN_NAME_QID = "pLsOiyHJ1eFwPJlNmLp4Bg"
FOLLOWING_QID = "gGVkcwUnM_ISWg3NIby2TA"
WEB_BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

BASIC_FEATURES = {
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

USER_BY_NAME_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
ZH_RE = re.compile(r"[\u4e00-\u9fff]")
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "you", "your", "our", "are", "was",
    "一个", "这个", "那个", "我们", "你们", "他们", "关注", "简介", "没有", "默认", "用户",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crawl a user's following list on X")
    p.add_argument("--user-url", required=True, help="User URL, e.g. https://x.com/vista8")
    p.add_argument("--state", default="auth_state.json", help="Playwright storage state path")
    p.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    p.add_argument("--out-dir", default="output", help="Output base directory")
    p.add_argument("--max-items", type=int, default=0, help="Max accounts to collect (0 means no hard limit)")
    p.add_argument("--max-pages", type=int, default=200, help="Max API pages")
    return p.parse_args()


def safe_name(text: str) -> str:
    text = re.sub(r"\s+", "_", str(text).strip())
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "", text)
    return text[:80] or "user"


def parse_handle(user_url: str) -> str:
    u = user_url.strip()
    if u.startswith("@"):
        return u[1:]
    if u.startswith("http://") or u.startswith("https://"):
        path = urlparse(u).path.strip("/")
        if not path:
            raise ValueError(f"Invalid --user-url: {user_url}")
        return path.split("/")[0].lstrip("@")
    return u.strip("/").lstrip("@")


def get_ct0(context: BrowserContext) -> str:
    for c in context.cookies():
        if c.get("name") == "ct0":
            return c.get("value", "")
    return ""


def api_get(context: BrowserContext, url: str, csrf: str, referer: str) -> dict:
    headers = {
        "authorization": f"Bearer {WEB_BEARER}",
        "x-csrf-token": csrf,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "zh-cn",
        "referer": referer,
    }
    resp = context.request.get(url, headers=headers, timeout=30000)
    if not resp.ok:
        raise RuntimeError(f"API request failed: {resp.status} {resp.text()[:200]}")
    return resp.json()


def build_user_by_name_url(handle: str) -> str:
    variables = {"screen_name": handle, "withGrokTranslatedBio": False}
    field_toggles = {"withPayments": False, "withAuxiliaryUserLabels": True}
    q = urlencode(
        {
            "variables": json.dumps(variables, separators=(",", ":"), ensure_ascii=False),
            "features": json.dumps(USER_BY_NAME_FEATURES, separators=(",", ":")),
            "fieldToggles": json.dumps(field_toggles, separators=(",", ":")),
        }
    )
    return f"https://x.com/i/api/graphql/{USER_BY_SCREEN_NAME_QID}/UserByScreenName?{q}"


def build_following_url(user_id: str, cursor: str | None = None) -> str:
    variables = {"userId": user_id, "count": 100, "includePromotedContent": False}
    if cursor:
        variables["cursor"] = cursor
    q = urlencode(
        {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(BASIC_FEATURES, separators=(",", ":")),
        }
    )
    return f"https://x.com/i/api/graphql/{FOLLOWING_QID}/Following?{q}"


def parse_user_by_name(data: dict) -> tuple[str, int]:
    u = data.get("data", {}).get("user", {}).get("result", {})
    rest_id = u.get("rest_id") or ""
    legacy = u.get("legacy", {}) or {}
    friends_count = int(legacy.get("friends_count") or 0)
    return rest_id, friends_count


def parse_following_page(data: dict) -> tuple[list[dict], str | None]:
    instructions = (
        data.get("data", {})
        .get("user", {})
        .get("result", {})
        .get("timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )

    users: list[dict] = []
    bottom_cursor = None

    for ins in instructions:
        for e in ins.get("entries", []):
            content = e.get("content", {})

            if content.get("__typename") == "TimelineTimelineCursor" and content.get("cursorType") == "Bottom":
                bottom_cursor = content.get("value")

            ur = content.get("itemContent", {}).get("user_results", {}).get("result")
            if not isinstance(ur, dict) or ur.get("__typename") != "User":
                continue

            core = ur.get("core", {}) or {}
            legacy = ur.get("legacy", {}) or {}
            handle = core.get("screen_name") or ""
            if not handle:
                continue

            users.append(
                {
                    "handle": handle,
                    "name": core.get("name") or "",
                    "bio": legacy.get("description") or "",
                    "verified": bool(ur.get("is_blue_verified", False)),
                    "profile_url": f"https://x.com/{handle}",
                }
            )

    return users, bottom_cursor


def collect_following_api(
    context: BrowserContext,
    handle: str,
    max_items: int,
    max_pages: int,
) -> tuple[list[dict], int]:
    csrf = get_ct0(context)
    if not csrf:
        raise RuntimeError("ct0 cookie not found; login state may be invalid")

    referer = f"https://x.com/{handle}/following"

    user_meta = api_get(context, build_user_by_name_url(handle), csrf, referer)
    user_id, profile_following_count = parse_user_by_name(user_meta)
    if not user_id:
        raise RuntimeError("Cannot resolve target user id via UserByScreenName")

    seen: dict[str, dict] = {}
    cursor = None

    for page in range(1, max_pages + 1):
        data = api_get(context, build_following_url(user_id, cursor), csrf, referer)
        batch, next_cursor = parse_following_page(data)

        new_count = 0
        for it in batch:
            key = it["handle"].lower()
            if key in seen:
                continue
            seen[key] = it
            new_count += 1
            if max_items > 0 and len(seen) >= max_items:
                print(f"Reached max items: {max_items}")
                return list(seen.values()), profile_following_count

        print(f"Page {page}/{max_pages}: +{new_count} new, total {len(seen)}")

        if not next_cursor:
            break
        if new_count == 0:
            break

        cursor = next_cursor

    return list(seen.values()), profile_following_count


def analyze_following(items: list[dict], owner_handle: str, profile_following_count: int) -> dict:
    total = len(items)
    bios = [i.get("bio", "") for i in items]
    names = [i.get("name", "") for i in items]

    verified_count = sum(1 for i in items if i.get("verified"))
    with_bio = sum(1 for b in bios if b.strip())
    zh_bio = sum(1 for b in bios if ZH_RE.search(b or ""))

    tokens = Counter()
    for txt in bios + names:
        for tk in WORD_RE.findall((txt or "").lower()):
            if tk in STOPWORDS or len(tk) < 2:
                continue
            tokens[tk] += 1

    first_letter = Counter()
    for i in items:
        h = (i.get("handle") or "").strip()
        if h:
            first_letter[h[0].lower()] += 1

    coverage = round(total / profile_following_count, 4) if profile_following_count > 0 else None

    return {
        "target_user": owner_handle,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_following_collected": total,
        "profile_following_count": profile_following_count,
        "coverage_ratio": coverage,
        "profile_stats": {
            "verified_count": verified_count,
            "verified_ratio": round(verified_count / total, 4) if total else 0,
            "bio_filled_count": with_bio,
            "bio_filled_ratio": round(with_bio / total, 4) if total else 0,
            "bio_has_chinese_count": zh_bio,
            "bio_has_chinese_ratio": round(zh_bio / total, 4) if total else 0,
        },
        "top_keywords": [{"keyword": k, "count": v} for k, v in tokens.most_common(60)],
        "handle_initial_distribution": [{"initial": k, "count": v} for k, v in first_letter.most_common()],
    }


def write_csv(path: Path, items: list[dict]) -> None:
    fields = ["handle", "name", "bio", "verified", "profile_url"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(items)


def write_detailed_md(path: Path, analysis: dict) -> None:
    s = analysis["profile_stats"]
    lines = [
        f"# Following Detailed Report: @{analysis['target_user']}",
        "",
        f"- Generated at (UTC): {analysis['generated_at_utc']}",
        f"- Profile following count: {analysis['profile_following_count']}",
        f"- Total following collected: {analysis['total_following_collected']}",
        f"- Coverage ratio: {analysis['coverage_ratio']}",
        f"- Verified ratio: {s['verified_ratio']}",
        f"- Bio filled ratio: {s['bio_filled_ratio']}",
        f"- Bio has Chinese ratio: {s['bio_has_chinese_ratio']}",
        "",
        "## Top keywords from name/bio",
    ]
    lines.extend([f"- {x['keyword']}: {x['count']}" for x in analysis["top_keywords"][:30]])
    lines.append("")
    lines.append("## Handle initials")
    lines.extend([f"- {x['initial']}: {x['count']}" for x in analysis["handle_initial_distribution"][:30]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_detailed_html(path: Path, analysis: dict) -> None:
    s = analysis["profile_stats"]
    kw_rows = "".join(
        f"<tr><td>{x['keyword']}</td><td>{x['count']}</td></tr>" for x in analysis["top_keywords"][:40]
    )
    html_doc = f"""<!doctype html>
<html lang=\"en\"><head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>@{analysis['target_user']} following detailed report</title>
<style>
body{{font-family:"IBM Plex Sans","Noto Sans",sans-serif;background:#f6f4ef;margin:0;color:#1f1f1c;}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px;}}
.card{{background:#fff;border:1px solid #dfd7ca;border-radius:12px;padding:16px;margin-bottom:14px;}}
table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #ece5d9;padding:8px;text-align:left}}th{{background:#f4efe6}}
</style></head>
<body><main class=\"wrap\">
<section class=\"card\"><h1>@{analysis['target_user']} Following 详细分析</h1>
<p>Profile following count: {analysis['profile_following_count']}</p>
<p>Total collected: {analysis['total_following_collected']}</p>
<p>Coverage ratio: {analysis['coverage_ratio']}</p>
<p>Verified ratio: {s['verified_ratio']}</p>
<p>Bio filled ratio: {s['bio_filled_ratio']}</p>
<p>Bio has Chinese ratio: {s['bio_has_chinese_ratio']}</p>
</section>
<section class=\"card\"><h2>Top Keywords</h2>
<table><thead><tr><th>Keyword</th><th>Count</th></tr></thead><tbody>{kw_rows}</tbody></table>
</section>
</main></body></html>"""
    path.write_text(html_doc, encoding="utf-8")


def main() -> None:
    args = parse_args()
    owner = parse_handle(args.user_url)

    out_base = Path(args.out_dir).expanduser().resolve()
    run_dir = out_base / f"following_{safe_name(owner)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    target_url = f"https://x.com/{owner}/following"
    print(f"Target user: @{owner}")
    print(f"Following URL: {target_url}")

    with sync_playwright() as p:
        context = create_context(p, args.state, args.headless)
        page = context.new_page()
        page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if not validate_auth_state(page):
            print("Authentication issue detected. Please refresh login state with login_x.py.")
            context.close()
            return

        items, profile_count = collect_following_api(
            context=context,
            handle=owner,
            max_items=args.max_items,
            max_pages=args.max_pages,
        )
        context.close()

    if not items:
        print("Warning: No following users were collected.")
        return

    analysis = analyze_following(items, owner, profile_count)

    json_path = run_dir / "results.json"
    csv_path = run_dir / "results.csv"
    detail_json = run_dir / "detailed_report.json"
    detail_md = run_dir / "detailed_report.md"
    detail_html = run_dir / "detailed_report.html"

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, items)
    detail_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    write_detailed_md(detail_md, analysis)
    write_detailed_html(detail_html, analysis)

    print("=" * 60)
    print(f"Done. Collected {len(items)} following users from @{owner}.")
    print(f"Profile shows following count: {profile_count}")
    print(f"Coverage ratio: {analysis['coverage_ratio']}")
    print(f"Results JSON:      {json_path}")
    print(f"Results CSV:       {csv_path}")
    print(f"Detailed JSON:     {detail_json}")
    print(f"Detailed Markdown: {detail_md}")
    print(f"Detailed HTML:     {detail_html}")
    print("=" * 60)


if __name__ == "__main__":
    main()
