#!/usr/bin/env python3
"""
Back4App Container Auto-Redeploy via GitHub OAuth Login
Logs in, checks if container is stopped (redeploy button visible), clicks it if so.
If container is running, just logs success and exits.
"""

import os
import sys
import time
import random
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# === Configuration ===
GH_USERNAME = os.environ.get("GH_USERNAME", "")
GH_PASSWORD = os.environ.get("GH_PASSWORD", "")
GH_2FA_SECRET = os.environ.get("GH_2FA_SECRET", "")
BACK4APP_URL = os.environ.get("BACK4APP_URL", "").strip()
if not BACK4APP_URL:
    print("ERROR: BACK4APP_URL environment variable is required!")
    sys.exit(1)
SCREENSHOT_DIR = os.environ.get("SCREENSHOT_DIR", "screenshots")

# Global step counter for screenshots
_step = 0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _redact_sensitive(page):
    """Mask sensitive info visible on the page before taking a screenshot."""
    # Collect all values that should be redacted
    app_id = ""
    try:
        app_id = urlparse(BACK4APP_URL).path.rstrip("/").rsplit("/", 1)[-1]
    except Exception:
        pass
    page.evaluate("""({appId, ghUser}) => {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
        const HEX_ID_RE = /\\b[0-9a-f]{24,}\\b/gi;
        const SHA256_RE = /\\b[0-9a-f]{64}\\b/gi;
        const EMAIL_RE = /[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Z|a-z]{2,}/g;
        const B4A_URL_RE = /[a-z0-9][a-z0-9\\-]*\\.b4a\\.run/gi;
        const REGISTRY_RE = /registry\\.containers\\.back4app\\.com\\/[^\\s]*/gi;
        while (walker.nextNode()) {
            let t = walker.currentNode;
            let v = t.nodeValue;
            if (!v) continue;
            v = v.replace(UUID_RE, '***');
            v = v.replace(SHA256_RE, '***');
            v = v.replace(HEX_ID_RE, '***');
            v = v.replace(EMAIL_RE, '***');
            v = v.replace(B4A_URL_RE, '***');
            v = v.replace(REGISTRY_RE, 'registry.***/***');
            if (appId && v.includes(appId)) {
                v = v.split(appId).join('***');
            }
            if (ghUser && v.includes(ghUser)) {
                v = v.split(ghUser).join('***');
            }
            if (v !== t.nodeValue) t.nodeValue = v;
        }
        // Also redact href/src attributes that contain sensitive info in <a> tags
        document.querySelectorAll('a[href]').forEach(a => {
            let h = a.getAttribute('href');
            if (h && (h.includes('.b4a.run') || (ghUser && h.includes(ghUser))
                       || (appId && h.includes(appId)))) {
                a.setAttribute('href', '#redacted');
            }
        });
    }""", {"appId": app_id, "ghUser": GH_USERNAME})


def shot(page, label):
    global _step
    _step += 1
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    name = f"{_step:02d}_{label}"
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    try:
        _redact_sensitive(page)
    except Exception:
        pass
    page.screenshot(path=path, full_page=True)
    log(f"Screenshot: {name}.png")


def safe_url(url):
    """Only keep scheme and hostname to avoid leaking IDs in paths or query strings."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/..."


def random_delay(low=0.5, high=1.5):
    time.sleep(random.uniform(low, high))


def inject_stealth(page):
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
    """)


def github_login(page):
    log("Filling GitHub credentials...")
    page.wait_for_selector("#login_field", timeout=30000)
    random_delay(0.5, 1.0)

    page.fill("#login_field", "")
    page.type("#login_field", GH_USERNAME, delay=random.randint(30, 80))
    random_delay(0.3, 0.7)

    page.fill("#password", "")
    page.type("#password", GH_PASSWORD, delay=random.randint(30, 80))
    random_delay(0.3, 0.7)

    page.click('input[type="submit"], button[type="submit"]')
    log("Submitted credentials, waiting...")
    shot(page, "credentials_submitted")
    random_delay(2.0, 4.0)

    handle_2fa(page)
    handle_oauth_authorize(page)


def handle_2fa(page):
    try:
        current_url = page.url
        if "verified-device" in current_url or "device-verification" in current_url:
            log("Device verification required! Waiting 60s...")
            shot(page, "device_verification")
            for _ in range(60):
                time.sleep(1)
                if "verified-device" not in page.url and "device-verification" not in page.url:
                    log("Device verification completed!")
                    break

        if "two-factor" not in page.url and "sessions/two-factor" not in page.url:
            totp_input = page.query_selector("#app_totp")
            if not totp_input:
                return

        if not GH_2FA_SECRET:
            log("2FA required but no GH_2FA_SECRET configured!")
            return

        import pyotp
        totp = pyotp.TOTP(GH_2FA_SECRET)

        for attempt in range(2):
            log(f"2FA attempt {attempt + 1}...")

            totp_input = page.query_selector("#app_totp")
            if not totp_input:
                try:
                    totp_input = page.wait_for_selector("#app_totp", timeout=5000)
                except Exception:
                    log("No TOTP input found")
                    return

            code = totp.now()
            log(f"Generated TOTP code: {code[:2]}****")

            totp_input.fill("")
            totp_input.type(code, delay=random.randint(50, 100))
            random_delay(0.5, 1.0)

            verify_btn = page.query_selector('button:has-text("Verify")')
            if verify_btn:
                verify_btn.click()
            log("2FA code submitted, waiting for navigation...")

            for _ in range(15):
                time.sleep(1)
                cur = page.url
                if "two-factor" not in cur and "sessions/two-factor" not in cur:
                    log(f"2FA passed! URL: {safe_url(cur)}")
                    shot(page, "2fa_passed")
                    return

            shot(page, f"2fa_retry_{attempt + 1}")
            log("Still on 2FA page, code may have been rejected")

            if attempt == 0:
                log("Waiting for next TOTP code window...")
                time.sleep(5)

        log("2FA failed after 2 attempts")
        shot(page, "2fa_failed")
    except Exception as e:
        log(f"2FA note: {e}")


def handle_oauth_authorize(page):
    try:
        if "login/oauth/authorize" in page.url:
            log("OAuth authorization page, clicking Authorize...")
            shot(page, "oauth_authorize")
            btn = page.wait_for_selector(
                'button:has-text("Authorize"), button[name="authorize"]',
                timeout=10000,
            )
            if btn:
                btn.click()
                log("Clicked Authorize")
                random_delay(3.0, 5.0)
    except PlaywrightTimeout:
        log("No authorize button (pre-authorized)")
    except Exception as e:
        log(f"OAuth note: {e}")


def handle_confirm_dialog(page):
    random_delay(1.0, 2.0)
    for sel in [
        'button:has-text("Confirm")',
        'button:has-text("Yes")',
        'button:has-text("OK")',
        'button:has-text("Deploy")',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                log(f"Clicking confirm: {sel}")
                btn.click()
                random_delay(2.0, 3.0)
                shot(page, "confirm_clicked")
                return
        except Exception:
            continue


def check_and_click_redeploy(page):
    log("Checking page for redeploy button...")
    shot(page, "app_page")

    body_text = ""
    try:
        body_text = page.inner_text("body")
    except Exception:
        pass

    for kw in ["available", "stopped", "error", "crashed", "sleeping", "inactive"]:
        if kw in body_text.lower():
            log(f"Container status: '{kw}'")

    if "redeploy" not in body_text.lower():
        log("No 'redeploy' text on page. Container is running.")
        return "running"

    log("Found 'redeploy' text! Looking for clickable element...")

    for sel in [
        'button:has-text("Redeploy")',
        'button:has-text("redeploy")',
        'button:has-text("Re-deploy")',
        'a:has-text("Redeploy")',
        'a:has-text("redeploy")',
        'a:has-text("Re-deploy")',
        'div:has-text("Redeploy")',
        'span:has-text("Redeploy")',
        '[class*="redeploy"]',
        '[data-testid*="redeploy"]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                text = el.inner_text().strip()
                log(f"Clicking: '{text}' ({sel})")
                shot(page, "before_redeploy")
                el.scroll_into_view_if_needed()
                random_delay(0.3, 0.8)
                el.click()
                log("Clicked redeploy!")
                random_delay(2.0, 4.0)
                shot(page, "after_redeploy")
                handle_confirm_dialog(page)
                return "redeployed"
        except Exception:
            continue

    try:
        elements = page.query_selector_all(
            "//*[contains(translate(text(), 'REDEPLOY', 'redeploy'), 'redeploy')]"
        )
        for el in elements:
            try:
                if el.is_visible():
                    tag = el.evaluate("el => el.tagName")
                    text = el.inner_text().strip()
                    log(f"XPath match: <{tag}> '{text}'")
                    el.scroll_into_view_if_needed()
                    el.click()
                    log("Clicked redeploy via XPath!")
                    random_delay(2.0, 4.0)
                    shot(page, "after_redeploy")
                    handle_confirm_dialog(page)
                    return "redeployed"
            except Exception:
                continue
    except Exception:
        pass

    log("'redeploy' text found but couldn't click any element.")
    return "found_but_not_clicked"


def main():
    if not GH_USERNAME or not GH_PASSWORD:
        log("ERROR: GH_USERNAME and GH_PASSWORD required!")
        sys.exit(1)

    log(f"Starting Back4App auto-redeploy")
    log(f"Target: <redacted>")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = context.new_page()
        inject_stealth(page)

        try:
            # Step 1: Open target page
            log("Navigating to Back4App...")
            page.goto(BACK4APP_URL, wait_until="networkidle", timeout=60000)
            random_delay(2.0, 3.0)
            shot(page, "initial_page")
            current_url = page.url
            log(f"URL: {safe_url(current_url)}")

            # Step 2: Login if needed
            if "login" in current_url.lower() or "signin" in current_url.lower() \
                    or "auth" in current_url.lower():
                log("Login page detected, starting GitHub OAuth...")
                shot(page, "login_page")

                for sel in [
                    'button:has-text("GitHub")',
                    'a:has-text("GitHub")',
                    'button:has-text("Continue with Github")',
                    'a:has-text("Sign in with GitHub")',
                    'button:has-text("Log in")',
                    'a:has-text("Log in")',
                ]:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            log(f"Clicking: '{btn.inner_text().strip()}'")
                            btn.click()
                            break
                    except Exception:
                        continue

                random_delay(3.0, 5.0)
                shot(page, "github_login_page")
                log(f"URL: {safe_url(page.url)}")

                if "github.com" in page.url:
                    github_login(page)
                    random_delay(3.0, 5.0)
                    log(f"URL after login: {safe_url(page.url)}")

                if "github.com" in page.url:
                    log("WARNING: Still on GitHub, login failed")
                    shot(page, "login_failed")
                    sys.exit(1)

                if "back4app.com" not in page.url:
                    try:
                        page.wait_for_url("**/back4app.com/**", timeout=30000)
                    except PlaywrightTimeout:
                        log(f"Redirect timeout. URL: {safe_url(page.url)}")

                random_delay(3.0, 5.0)
                shot(page, "login_complete")
            else:
                log("No login needed")

            # Step 3: Ensure we're on the target app page
            if BACK4APP_URL not in page.url:
                log("Navigating to target app page...")
                page.goto(BACK4APP_URL, wait_until="networkidle", timeout=60000)
                random_delay(3.0, 5.0)

            page.wait_for_load_state("domcontentloaded")
            random_delay(3.0, 5.0)

            # Step 4: Check for redeploy
            result = check_and_click_redeploy(page)

            if result == "redeployed":
                log("SUCCESS: Container was stopped, redeploy clicked!")
                shot(page, "done_redeployed")
                gh_output = os.environ.get("GITHUB_OUTPUT", "")
                if gh_output:
                    with open(gh_output, "a") as f:
                        f.write("redeployed=true\n")
                    log("Set GITHUB_OUTPUT redeployed=true")
            elif result == "running":
                log("OK: Container is running, no redeploy needed.")
                shot(page, "done_running")
            else:
                log(f"NOTE: Result={result}")
                shot(page, "done_unknown")

        except Exception as e:
            log(f"ERROR: {e}")
            shot(page, "error")
            raise
        finally:
            context.close()
            browser.close()

    log("Done!")


if __name__ == "__main__":
    main()
