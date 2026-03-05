#!/usr/bin/env python3
"""Search x.com using an existing Chrome session via CDP."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener

from playwright.sync_api import sync_playwright


def parse_args():
    parser = argparse.ArgumentParser(description="Search x.com using existing Chrome browser")
    parser.add_argument("--keyword", required=True, help="Search keyword")
    parser.add_argument("--max-items", type=int, default=200, help="Max tweets to collect")
    parser.add_argument("--out-dir", default="output", help="Output directory")
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome CDP endpoint, default: http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--chrome-path",
        default="/usr/bin/google-chrome",
        help="Chrome executable path for auto-launch",
    )
    parser.add_argument(
        "--user-data-dir",
        default="chrome_profile",
        help="Chrome user data dir used when auto-launching Chrome",
    )
    parser.add_argument(
        "--auto-launch",
        action="store_true",
        help="Auto launch Chrome with remote debugging if CDP endpoint is unavailable",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=20,
        help="Seconds to wait for CDP endpoint after auto-launch",
    )
    return parser.parse_args()


def cdp_ready(cdp_url: str) -> bool:
    version_url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(version_url, timeout=1.5) as resp:
            return resp.status == 200
    except URLError:
        return False
    except Exception:
        return False


def launch_chrome_for_cdp(chrome_path: str, user_data_dir: Path, cdp_port: int) -> subprocess.Popen:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "https://x.com/home",
    ]
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        # Headless fallback is needed on server environments with no display.
        cmd.insert(-1, "--headless=new")
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def parse_cdp_port(cdp_url: str) -> int:
    tail = cdp_url.rstrip("/").split(":")[-1]
    return int(tail)


def can_connect_over_cdp(cdp_url: str) -> bool:
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=3000)
            browser.close()
        return True
    except Exception:
        return False


def wait_cdp_ready(cdp_url: str, wait_seconds: int) -> bool:
    for _ in range(max(1, wait_seconds * 2)):
        if cdp_ready(cdp_url):
            return True
        time.sleep(0.5)
    return False


def main():
    args = parse_args()

    # Create output directory
    out_base = Path(args.out_dir).expanduser().resolve()
    run_dir = out_base / f"{args.keyword}_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Construct search URL
    encoded_keyword = quote(args.keyword, safe="")
    search_url = f"https://x.com/search?q={encoded_keyword}&src=typed_query&f=live"

    print(f"Using existing Chrome browser to search: {search_url}")
    print(f"CDP endpoint: {args.cdp_url}")
    print("Please make sure you're already logged into X in your Chrome browser.")

    chrome_proc = None
    if not cdp_ready(args.cdp_url):
        if not args.auto_launch:
            print("\nCDP endpoint is not reachable.")
            print("Option A: start Chrome manually with remote debugging:")
            print(
                "  google-chrome --remote-debugging-port=9222 "
                "--remote-debugging-address=127.0.0.1"
            )
            print("Option B: rerun with --auto-launch for one-command startup.")
            raise SystemExit(2)

        try:
            cdp_port = parse_cdp_port(args.cdp_url)
        except ValueError as exc:
            raise SystemExit(f"Invalid --cdp-url: {args.cdp_url}") from exc

        requested_profile_dir = Path(args.user_data_dir).expanduser().resolve()
        chrome_proc = launch_chrome_for_cdp(
            chrome_path=args.chrome_path,
            user_data_dir=requested_profile_dir,
            cdp_port=cdp_port,
        )
        print(f"Started Chrome with remote debugging, waiting for endpoint... (profile: {requested_profile_dir})")

        ready = wait_cdp_ready(args.cdp_url, args.wait_seconds)
        if not ready:
            # Fallback: some environments fail /json/version checks but CDP is connectable.
            if can_connect_over_cdp(args.cdp_url):
                ready = True

        retry_profile = requested_profile_dir.parent / f"{requested_profile_dir.name}_fresh_{int(time.time())}"
        can_retry_with_fresh_profile = not ready and requested_profile_dir.exists()
        if can_retry_with_fresh_profile:
            print(
                "Endpoint is still unavailable. "
                f"Retrying once with a fresh profile: {retry_profile}"
            )
            chrome_proc = launch_chrome_for_cdp(
                chrome_path=args.chrome_path,
                user_data_dir=retry_profile,
                cdp_port=cdp_port,
            )
            ready = wait_cdp_ready(args.cdp_url, args.wait_seconds)
            if not ready and can_connect_over_cdp(args.cdp_url):
                ready = True

        if not ready:
            stderr = "(empty)"
            if chrome_proc and chrome_proc.stderr:
                if chrome_proc.poll() is not None:
                    stderr = chrome_proc.stderr.read().strip() or "(empty)"
                else:
                    stderr = (
                        "Chrome process is still running but endpoint probe failed. "
                        "Try increasing --wait-seconds or pass a different --cdp-url."
                    )
            raise SystemExit(
                f"CDP endpoint did not become ready within {args.wait_seconds}s.\n"
                f"Chrome stderr:\n{stderr}\n"
                "Tip: this often means the selected Chrome profile cannot start. "
                "Try --user-data-dir with a new empty directory."
            )

    with sync_playwright() as p:
        # Connect to existing Chrome browser
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        # Navigate to search URL
        page.goto(search_url, wait_until="domcontentloaded")
        print("Page loaded. Please wait while we collect results...")

        # Wait for initial content
        time.sleep(3)

        tweets = []
        seen_text = set()

        idle_scrolls = 0
        while len(tweets) < args.max_items and idle_scrolls < 10:
            tweet_elements = page.query_selector_all('article[data-testid="tweet"]')
            before = len(tweets)
            for tweet in tweet_elements:
                if len(tweets) >= args.max_items:
                    break
                try:
                    text_element = tweet.query_selector('div[data-testid="tweetText"]')
                    text = (text_element.inner_text() if text_element else "").strip()
                    if not text or text in seen_text:
                        continue
                    seen_text.add(text)
                    tweets.append({"id": len(tweets) + 1, "text": text, "url": search_url})
                    print(f"Collected tweet {len(tweets)}: {text[:60]}...")
                except Exception as e:
                    print(f"Error extracting tweet: {e}")

            if len(tweets) == before:
                idle_scrolls += 1
            else:
                idle_scrolls = 0

            page.mouse.wheel(0, 2500)
            time.sleep(1.2)

        browser.close()

    if chrome_proc:
        print("Leaving auto-launched Chrome running so you keep login/session state.")

    # Save results
    output_file = run_dir / "results.json"
    output_file.write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone! Collected {len(tweets)} tweets.")
    print(f"Results saved to: {output_file}")


if __name__ == "__main__":
    main()
