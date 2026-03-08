#!/usr/bin/env python3
"""Hydrate an existing results.json file with full tweet text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from search_x import create_context
from tweet_fulltext import hydrate_items_with_fulltext


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read an existing results.json and hydrate full tweet text")
    parser.add_argument("--input", required=True, help="Path to results.json or a run directory containing it")
    parser.add_argument("--state", default="auth_state_cookie.json", help="Playwright storage state path")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--delay-ms", type=int, default=1200, help="Pause after opening each tweet detail page")
    parser.add_argument("--checkpoint-every", type=int, default=10, help="Write progress every N tweets")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    json_path = input_path / "results.json" if input_path.is_dir() else input_path
    if not json_path.exists():
        raise FileNotFoundError(f"results.json not found: {json_path}")

    run_dir = json_path.parent
    items = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"invalid results payload: {json_path}")

    with sync_playwright() as p:
        context = create_context(p, args.state, args.headless)
        items = hydrate_items_with_fulltext(
            context=context,
            items=items,
            run_dir=run_dir,
            raw_name="results_stage1.json",
            final_name="results.json",
            checkpoint_every=args.checkpoint_every,
            delay_ms=args.delay_ms,
            logger=print,
        )
        context.close()

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Hydrated results: {json_path}")
    print(f"Progress file:    {run_dir / 'fulltext_progress.json'}")


if __name__ == "__main__":
    main()
