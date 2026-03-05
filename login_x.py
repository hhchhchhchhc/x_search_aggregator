#!/usr/bin/env python3
"""Login helper: open X in browser and persist auth state for later scraping."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Log into x.com and save storage state.")
    parser.add_argument(
        "--state",
        default="auth_state.json",
        help="Path to save Playwright storage state (default: auth_state.json)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for manual login before saving state (default: 180)",
    )
    parser.add_argument(
        "--persistent-dir",
        default="~/.config/google-chrome",
        help="Chrome user data dir for persistent profile (default: ~/.config/google-chrome).",
    )
    parser.add_argument(
        "--chrome-path",
        default="/usr/bin/google-chrome",
        help="Path to official Chrome executable (default: /usr/bin/google-chrome)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state_path = Path(args.state).expanduser().resolve()

    with sync_playwright() as p:
        profile_dir = Path(args.persistent_dir).expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        print(f"Using Chrome profile dir: {profile_dir}")
        print("If launch fails, close all existing Chrome windows and retry.")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            executable_path=args.chrome_path,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-software-rasterizer",
                "--use-gl=swiftshader",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        page = context.new_page()
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")

        print("=" * 60)
        print("Please finish login in the opened browser window.")
        print(f"After login, keep the page open for up to {args.timeout} seconds.")
        print("=" * 60)

        page.wait_for_timeout(args.timeout * 1000)
        context.storage_state(path=str(state_path))
        print(f"Saved login state to: {state_path}")

        context.close()


if __name__ == "__main__":
    main()
