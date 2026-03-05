#!/usr/bin/env python3
"""Use existing Chrome browser for X scraping with Playwright."""

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

def create_existing_chrome_context():
    """Create Playwright context using existing Chrome installation."""
    with sync_playwright() as p:
        # Launch existing Chrome with debugging port
        browser = p.chromium.launch(
            headless=False,
            executable_path="/usr/bin/google-chrome",
            args=[
                "--remote-debugging-port=9222",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-popup-blocking"
            ]
        )
        
        # Create a new context
        context = browser.new_context()
        page = context.new_page()
        
        return browser, context, page

def save_auth_state(context, state_path="auth_state_existing.json"):
    """Save authentication state from existing browser session."""
    state_path = Path(state_path)
    context.storage_state(path=str(state_path))
    print(f"Authentication state saved to: {state_path}")

def main():
    """Main function to capture auth state from existing Chrome."""
    print("Launching existing Chrome browser...")
    print("Please log in to X.com in the opened browser window.")
    print("After successful login, press Enter here to save the auth state.")
    
    browser, context, page = create_existing_chrome_context()
    page.goto("https://x.com")
    
    try:
        input("Press Enter after you've logged in to X...")
        save_auth_state(context)
        print("Auth state saved successfully!")
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
    finally:
        context.close()
        browser.close()

if __name__ == "__main__":
    main()