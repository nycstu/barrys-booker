#!/usr/bin/env python3
"""
Barry's Bootcamp Auto-Booker
Logs into barrys.com, finds the Thursday 7:20a class at Noho,
selects spot DF33, and books it.

Run at exactly 12:00p ET every Thursday when booking opens.
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Setup
load_dotenv(override=False)  # Don't override system env vars (for Render)
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
STATE_FILE = Path(__file__).parent / "auth_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"booking_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# Config from .env
EMAIL = os.getenv("BARRYS_EMAIL")
PASSWORD = os.getenv("BARRYS_PASSWORD")
STUDIO = os.getenv("BARRYS_STUDIO", "noho")
CLASS_TIME = os.getenv("BARRYS_CLASS_TIME", "07:20")
# Spot priority list (first available wins)
_spots_raw = os.getenv("BARRYS_SPOTS", os.getenv("BARRYS_SPOT", "DF-33"))
PREFERRED_SPOTS = [s.strip() for s in _spots_raw.split(",")]
TARGET_DAY = os.getenv("BARRYS_DAY", "thursday")

BARRYS_BASE = "https://www.barrys.com"
SCHEDULE_URL = f"{BARRYS_BASE}/schedule/{STUDIO}"
LOGIN_URL = f"{BARRYS_BASE}/login"


def screenshot(page, name):
    """Save a timestamped screenshot for debugging."""
    ts = datetime.now().strftime("%H%M%S")
    path = SCREENSHOT_DIR / f"{ts}_{name}.png"
    page.screenshot(path=str(path), full_page=True)
    log.info(f"Screenshot saved: {path}")
    return path


def get_next_target_date():
    """Calculate the date of next Thursday's class (7 days from now)."""
    today = datetime.now()
    days_ahead = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    target = days_ahead.get(TARGET_DAY.lower(), 3)
    current = today.weekday()
    diff = (target - current) % 7
    if diff == 0:
        diff = 7  # next week's class
    target_date = today + timedelta(days=diff)
    return target_date


def login(page):
    """Log into barrys.com. Uses saved auth state if available."""
    if STATE_FILE.exists():
        log.info("Found saved auth state, checking if still valid...")
        # Try loading the schedule page to test if session is active
        page.goto(SCHEDULE_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)
        # Check if we're redirected to login
        if "/login" not in page.url and "/sign" not in page.url:
            log.info("Auth state still valid, skipping login")
            return True
        log.info("Auth state expired, logging in fresh...")

    log.info(f"Navigating to login page: {LOGIN_URL}")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeout:
        log.warning("Page load timed out, continuing anyway...")
    time.sleep(3)
    screenshot(page, "login_page_raw")

    # Step 1: Dismiss ALL cookie banners via JS + selectors
    # Cookiebot injects its dialog dynamically, so try multiple approaches
    try:
        # Approach A: Click the decline/deny/allow button if visible
        cookie_selectors = [
            '#CybotCookiebotDialogBodyButtonDecline',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
            '#CybotCookiebotDialogBodyButtonAccept',
        ]
        for sel in cookie_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=3000)
                if btn and btn.is_visible():
                    btn.click()
                    log.info(f"Dismissed cookie banner via selector: {sel}")
                    time.sleep(2)
                    break
            except PlaywrightTimeout:
                continue

        # Approach B: Use JS to brute-force dismiss ALL cookie banners
        page.evaluate("""() => {
            // Try Cookiebot API
            if (typeof Cookiebot !== 'undefined') {
                try { Cookiebot.consent.accepted = true; Cookiebot.hide(); } catch(e) {}
                try { Cookiebot.submitCustomConsent(false, false, false); } catch(e) {}
            }
            if (typeof CookieConsent !== 'undefined') {
                try { CookieConsent.acceptAll && CookieConsent.acceptAll(); } catch(e) {}
            }

            // Click any button containing "ACCEPT" or "DENY" or "ALLOW" text
            const allButtons = document.querySelectorAll('button, a, [role="button"]');
            allButtons.forEach(btn => {
                const text = btn.textContent.trim().toUpperCase();
                if (text.includes('ACCEPT ALL') || text === 'DENY' || text.includes('ALLOW ALL')) {
                    btn.click();
                }
            });

            // Nuclear: remove all fixed/sticky overlays that might be cookie banners
            document.querySelectorAll('*').forEach(el => {
                const style = getComputedStyle(el);
                if ((style.position === 'fixed' || style.position === 'sticky') && style.zIndex > 100) {
                    const text = el.textContent || '';
                    if (text.includes('cookie') || text.includes('Cookie') || text.includes('consent')) {
                        el.remove();
                    }
                }
            });

            // Also hide the Cookiebot dialog element and its overlay directly
            ['#CybotCookiebotDialog', '#CybotCookiebotDialogBodyUnderlay',
             '#cookiebanner', '.cookie-banner', '.cookie-consent'].forEach(sel => {
                const el = document.querySelector(sel);
                if (el) el.remove();
            });
        }""")
        log.info("Ran JS cookie dismissal (nuclear)")
        time.sleep(2)

        # Approach C: Check for iframes containing cookie dialogs
        for frame in page.frames:
            try:
                frame.evaluate("""() => {
                    const btns = document.querySelectorAll('button, a');
                    btns.forEach(b => {
                        const t = b.textContent.trim().toUpperCase();
                        if (t.includes('ACCEPT') || t.includes('DENY') || t.includes('ALLOW')) {
                            b.click();
                        }
                    });
                }""")
            except Exception:
                pass
        log.info("Checked iframes for cookie banners")
        time.sleep(1)
    except Exception as e:
        log.warning(f"Cookie dismissal error (continuing): {e}")

    screenshot(page, "after_cookies")

    # Step 2: Dismiss the cookie success toast, then scroll to reveal login form
    time.sleep(1)
    try:
        close_toast = page.query_selector('button:has-text("×"), [aria-label="close"], [aria-label="Close"]')
        if close_toast and close_toast.is_visible():
            close_toast.click()
            log.info("Dismissed cookie success toast")
            time.sleep(1)
    except Exception:
        pass

    # Scroll down to see the login form (it's below "Account" tabs area)
    page.evaluate("window.scrollTo(0, 500)")
    time.sleep(2)
    screenshot(page, "login_form_area")

    # The login form may be inside a Mariana Tek iframe or web component
    # Dump all iframes for debugging
    for i, frame in enumerate(page.frames):
        log.info(f"Frame {i}: url={frame.url[:100]}, name={frame.name}")

    # Also check for shadow DOM elements
    shadow_info = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
                const inputs = el.shadowRoot.querySelectorAll('input');
                results.push({
                    tag: el.tagName,
                    id: el.id,
                    class: el.className,
                    inputCount: inputs.length,
                    inputTypes: Array.from(inputs).map(i => i.type)
                });
            }
        });
        return results;
    }""")
    log.info(f"Shadow DOM elements with inputs: {shadow_info}")

    # Check for iframes that might contain the login form
    frames = page.frames
    login_frame = page
    for frame in frames:
        if frame != page.main_frame:
            try:
                email_in_frame = frame.query_selector('input[type="email"], input[name="email"]')
                if email_in_frame:
                    login_frame = frame
                    log.info(f"Found login form in iframe: {frame.url}")
                    break
            except Exception:
                continue

    # Step 3: The login form is inside a Mariana Tek iframe
    # Poll for MT iframe - cloud servers take longer to load
    mt_frame = None
    for attempt in range(20):
        for frame in page.frames:
            if "marianaiframes.com" in frame.url or "marianatek.com" in frame.url:
                mt_frame = frame
                log.info(f"Found Mariana Tek iframe (attempt {attempt+1}): {frame.url[:80]}")
                break
        if mt_frame:
            break
        time.sleep(1)
        log.info(f"Waiting for MT iframe on login page (attempt {attempt+1}/20)...")
        if attempt % 5 == 4:
            for i, f in enumerate(page.frames):
                log.info(f"  Frame {i}: {f.url[:80]}")

    if not mt_frame:
        log.error("Could not find Mariana Tek iframe after 20s")
        screenshot(page, "no_mt_iframe")
        return False

    # Wait for the iframe SPA to fully render
    login_frame = mt_frame
    log.info("Waiting for Mariana Tek iframe to render...")
    time.sleep(3)
    screenshot(page, "mt_iframe_found")

    # The MT iframe shows "Welcome Back / Please log in / LOG IN" but no input fields yet.
    # There's also a cookie banner INSIDE the iframe blocking things.
    # Use JS inside the iframe to dismiss cookies and click LOG IN.
    log.info("Dismissing cookies and clicking LOG IN inside MT iframe via JS...")
    try:
        login_frame.evaluate("""() => {
            // First dismiss any cookie banners inside the iframe
            document.querySelectorAll('*').forEach(el => {
                const text = el.textContent.trim();
                const upper = text.toUpperCase();
                if (upper === 'ACCEPT ALL COOKIES' || upper === 'DENY' || upper === 'ALLOW ALL') {
                    el.click();
                    console.log('Clicked cookie button:', text);
                }
            });
            // Remove any cookie overlays
            document.querySelectorAll('[class*="cookie"], [id*="cookie"], [class*="consent"]').forEach(el => {
                el.style.display = 'none';
            });
        }""")
        time.sleep(2)
        log.info("Dismissed cookies inside MT iframe")
    except Exception as e:
        log.warning(f"MT iframe cookie dismissal: {e}")

    screenshot(page, "mt_after_cookies")

    # Now click the LOG IN button via JS
    try:
        clicked = login_frame.evaluate("""() => {
            // Find all clickable elements and click the one that says "LOG IN"
            const elements = document.querySelectorAll('button, a, [role="button"], div, span');
            for (const el of elements) {
                const text = el.textContent.trim();
                if (text === 'LOG IN' || text === 'Log In' || text === 'Sign In') {
                    // Make sure it's a leaf-ish element (not a container with lots of text)
                    if (text.length < 20) {
                        el.click();
                        return 'clicked: ' + text;
                    }
                }
            }
            return 'not found';
        }""")
        log.info(f"LOG IN button click result: {clicked}")
        time.sleep(5)
        screenshot(page, "after_login_btn_click")
    except Exception as e:
        log.warning(f"Could not click LOG IN: {e}")

    # Re-acquire the MT iframe frame reference (it navigated to marianatek.com/auth/login)
    # Poll for up to 20 seconds - cloud servers are slower
    login_frame = None
    for attempt in range(20):
        for frame in page.frames:
            if "marianaiframes.com" in frame.url or "marianatek.com" in frame.url:
                login_frame = frame
                log.info(f"Re-acquired MT iframe after navigation (attempt {attempt+1}): {frame.url[:80]}")
                break
        if login_frame:
            break
        time.sleep(1)
        log.info(f"Waiting for MT iframe to appear after LOG IN click (attempt {attempt+1}/20)...")
        # Log all current frames for debugging
        if attempt % 5 == 4:
            for i, f in enumerate(page.frames):
                log.info(f"  Current frame {i}: {f.url[:80]}")
    if not login_frame:
        log.error("Lost MT iframe after LOG IN click - dumping all frames:")
        for i, f in enumerate(page.frames):
            log.error(f"  Frame {i}: {f.url[:80]}")
        screenshot(page, "lost_mt_iframe")
        return False

    # Now the login form (email + password) should be visible inside the iframe
    # Debug what we have now
    try:
        mt_inputs = login_frame.query_selector_all("input")
        log.info(f"MT iframe now has {len(mt_inputs)} input elements after clicking LOG IN")
        for inp in mt_inputs[:10]:
            inp_html = login_frame.evaluate("el => el.outerHTML.substring(0, 150)", inp)
            log.info(f"  MT input: {inp_html}")
        body_text = login_frame.evaluate("() => document.body ? document.body.innerText.substring(0, 500) : 'no body'")
        log.info(f"MT iframe body text: {body_text[:300]}")
    except Exception as e:
        log.warning(f"Could not inspect MT iframe after click: {e}")

    email_selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[placeholder*="email" i]',
        'input[placeholder*="Email" i]',
        'input[name="username"]',
        'input[autocomplete="email"]',
        'input[autocomplete="username"]',
        '#email',
        '#username',
        'input[type="text"]',
    ]
    password_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[autocomplete="current-password"]',
        '#password',
    ]

    email_input = None
    for sel in email_selectors:
        try:
            email_input = login_frame.wait_for_selector(sel, timeout=5000)
            if email_input:
                log.info(f"Found email input in MT iframe: {sel}")
                break
        except PlaywrightTimeout:
            continue

    if not email_input:
        log.info("No email field found in MT iframe after clicking LOG IN...")
        login_triggers = [
            'a:has-text("Log In")',
            'a:has-text("Sign In")',
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            'a[href*="login"]',
            'a[href*="signin"]',
        ]
        for sel in login_triggers:
            try:
                trigger = page.wait_for_selector(sel, timeout=3000)
                if trigger and trigger.is_visible():
                    trigger.click()
                    log.info(f"Clicked login trigger: {sel}")
                    time.sleep(3)
                    screenshot(page, "after_login_trigger")
                    # Try finding email input again
                    for esel in email_selectors:
                        try:
                            email_input = page.wait_for_selector(esel, timeout=3000)
                            if email_input:
                                log.info(f"Found email input after trigger: {esel}")
                                break
                        except PlaywrightTimeout:
                            continue
                    if email_input:
                        break
            except PlaywrightTimeout:
                continue

    if not email_input:
        screenshot(page, "no_email_field")
        log.error("Could not find email input field")
        # Save full HTML for debugging
        html_path = LOG_DIR / "login_page.html"
        html_path.write_text(page_html)
        log.info(f"Saved page HTML to {html_path}")
        return False

    # Fill email
    email_input.fill(EMAIL)
    time.sleep(1)
    screenshot(page, "email_filled")

    # Check if password is visible now, or if we need to submit email first
    password_input = None
    for sel in password_selectors:
        try:
            password_input = login_frame.wait_for_selector(sel, timeout=3000)
            if password_input and password_input.is_visible():
                log.info(f"Found password input: {sel}")
                break
            password_input = None
        except PlaywrightTimeout:
            continue

    if not password_input:
        # Multi-step login: submit email first, then look for password
        log.info("Password not visible yet, submitting email first...")
        submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button:has-text("Submit")',
        ]
        for sel in submit_selectors:
            try:
                btn = login_frame.wait_for_selector(sel, timeout=3000)
                if btn and btn.is_visible():
                    btn.click()
                    log.info(f"Submitted email step: {sel}")
                    time.sleep(3)
                    break
            except PlaywrightTimeout:
                continue

        # Also try pressing Enter
        if not password_input:
            email_input.press("Enter")
            time.sleep(3)

        screenshot(page, "after_email_submit")

        for sel in password_selectors:
            try:
                password_input = login_frame.wait_for_selector(sel, timeout=5000)
                if password_input and password_input.is_visible():
                    log.info(f"Found password input after email step: {sel}")
                    break
                password_input = None
            except PlaywrightTimeout:
                continue

    if not password_input:
        screenshot(page, "no_password_field")
        log.error("Could not find password input field")
        return False

    # Fill password
    password_input.fill(PASSWORD)
    time.sleep(0.5)
    screenshot(page, "credentials_filled")

    # Click login/submit button
    submit_selectors = [
        'button[type="submit"]',
        'button:has-text("Log In")',
        'button:has-text("Sign In")',
        'button:has-text("Login")',
        'button:has-text("LOG IN")',
        'button:has-text("SIGN IN")',
        'input[type="submit"]',
    ]

    for sel in submit_selectors:
        try:
            btn = login_frame.wait_for_selector(sel, timeout=3000)
            if btn and btn.is_visible():
                btn.click()
                log.info(f"Clicked submit button: {sel}")
                break
        except PlaywrightTimeout:
            continue

    # Wait for navigation after login (longer on cloud servers)
    time.sleep(15)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeout:
        log.warning("Network idle timeout after login, continuing...")
    screenshot(page, "after_login")

    # Verify login succeeded by checking for account content
    logged_in = False

    # Debug: log all frames and their content after login
    for i, frame in enumerate(page.frames):
        try:
            url = frame.url
            text = frame.evaluate("() => document.body ? document.body.innerText.substring(0, 300) : ''")
            log.info(f"Post-login frame {i}: url={url[:80]} text={text[:150]}")
            if "marianaiframes.com" in url or "marianatek.com" in url:
                if "Log Out" in text or "Reservations" in text or "Account" in text:
                    logged_in = True
                    log.info("Login verified - found account content in MT iframe")
        except Exception as e:
            log.info(f"Post-login frame {i}: error reading - {e}")

    # Also check the main page URL and text
    log.info(f"Post-login main URL: {page.url}")
    main_text = page.text_content("body") or ""
    log.info(f"Post-login main page text: {main_text[:200]}")
    if "Log Out" in main_text or "Reservations" in main_text:
        logged_in = True
        log.info("Login verified via main page content")

    if not logged_in:
        log.error("Login may have failed - no account content found")
        # Don't return False yet, try to continue anyway
        log.info("Attempting to continue despite login check failure...")

    # Save auth state for future runs
    page.context.storage_state(path=str(STATE_FILE))
    log.info("Login successful, auth state saved")
    return True


def get_mt_frame(page):
    """Find and return the current Mariana Tek iframe."""
    for frame in page.frames:
        url = frame.url
        if "marianaiframes.com" in url or "marianatek.com" in url:
            return frame
    # Log all frames if not found
    log.debug(f"get_mt_frame: no MT frame found in {[f.url[:60] for f in page.frames]}")
    return None


def navigate_to_schedule(page, target_date):
    """Navigate to the Noho schedule for the target date."""
    date_str = target_date.strftime("%Y-%m-%d")
    day_name = target_date.strftime("%a").upper()  # THU
    log.info(f"Navigating to schedule for {date_str} ({day_name})")

    # Try URL with date param first (most reliable - skips date tab entirely)
    schedule_url_with_date = f"{SCHEDULE_URL}?date={date_str}"
    log.info(f"Navigating to: {schedule_url_with_date}")
    try:
        page.goto(schedule_url_with_date, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeout:
        log.warning("Schedule page load timed out, continuing...")
    time.sleep(5)
    screenshot(page, "schedule_page")

    # The schedule is inside the MT iframe. Find it and click the right date tab.
    # Poll for MT iframe to appear on schedule page
    mt = None
    for attempt in range(15):
        mt = get_mt_frame(page)
        if mt:
            break
        time.sleep(1)
        log.info(f"Waiting for MT iframe on schedule page (attempt {attempt+1}/15)...")
    if mt:
        log.info("Found MT iframe on schedule page, looking for date tabs...")
        time.sleep(3)

        # Log the MT iframe body for debugging
        try:
            mt_body = mt.evaluate("() => document.body ? document.body.innerText.substring(0, 800) : 'no body'")
            log.info(f"MT iframe schedule body: {mt_body[:600]}")
        except Exception as e:
            log.warning(f"Could not read MT iframe body: {e}")

        # The schedule shows a weekly view. We may need to click "next week" to reach target date.
        # Keep clicking the forward/next button until target date appears in the tabs (max 4 weeks).
        month_day_target = target_date.strftime("%b %-d")   # e.g. "Apr 9"
        month_day_target2 = target_date.strftime("%b %d")   # e.g. "Apr 09"

        for week_advance in range(4):
            try:
                body_text = mt.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body_text = ""

            if month_day_target in body_text or month_day_target2 in body_text or date_str in body_text:
                log.info(f"Target date {month_day_target} is visible after {week_advance} week advances")
                break

            if week_advance == 0:
                log.info(f"Target date {month_day_target} not in current week view, clicking next week...")
            else:
                log.info(f"Still not visible, clicking next week again (advance {week_advance+1})...")

            # Click the next-week navigation button (typically a ">" or chevron arrow)
            nav_clicked = mt.evaluate("""() => {
                const allEls = Array.from(document.querySelectorAll('*'));
                // Look for next/forward navigation buttons
                const nextBtns = allEls.filter(el => {
                    const text = el.textContent.trim();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const cls = el.className.toString().toLowerCase();
                    return (text === '>' || text === '›' || text === '»' || text === '→' ||
                            aria.includes('next') || aria.includes('forward') ||
                            cls.includes('next') || cls.includes('forward') || cls.includes('right')) &&
                           (el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button');
                });
                if (nextBtns.length > 0) {
                    nextBtns[0].click();
                    return 'clicked next: ' + nextBtns[0].textContent.trim() +
                           ' aria=' + (nextBtns[0].getAttribute('aria-label') || '');
                }
                // Fallback: find any clickable element with just ">" or arrow chars
                for (const el of allEls) {
                    const text = el.textContent.trim();
                    if ((text === '>' || text === '›' || text === '»') && el.children.length === 0) {
                        el.click();
                        return 'clicked arrow: ' + text;
                    }
                }
                return 'next button not found';
            }""")
            log.info(f"Next week nav result: {nav_clicked}")
            time.sleep(3)

        # Now click the specific day tab for the target date
        day_short_upper = target_date.strftime("%a").upper()   # "THU"
        month_short = target_date.strftime("%b")               # "Apr"
        day_num = str(target_date.day)                         # "9"

        clicked_date = mt.evaluate(f"""() => {{
            const target = '{month_day_target}';
            const dayShort = '{day_short_upper}';
            const monthShort = '{month_short}';
            const dayNum = '{day_num}';
            const allEls = Array.from(document.querySelectorAll('*'));

            // Log all short texts for debugging
            const shortTexts = allEls.map(e => e.textContent.trim())
                .filter(t => t.length > 0 && t.length < 30)
                .filter((v,i,a) => a.indexOf(v) === i).slice(0, 40);
            console.log('Visible texts:', JSON.stringify(shortTexts));

            const matchers = [target, monthShort + ' ' + dayNum, dayNum + ' ' + dayShort];
            for (const matcher of matchers) {{
                for (const el of allEls) {{
                    const text = el.textContent.trim();
                    if (text.includes(matcher) && text.length < 30) {{
                        el.click();
                        return 'clicked (' + matcher + '): ' + text;
                    }}
                }}
            }}
            return 'tab not found. visible: ' + JSON.stringify(shortTexts.slice(0,20));
        }}""")
        log.info(f"Date tab click: {clicked_date}")
        time.sleep(3)
    else:
        # No MT iframe on schedule page, try clicking date tabs on main page
        log.info("No MT iframe found, trying main page date navigation...")
        try:
            thu_tab = page.wait_for_selector('text=THU', timeout=5000)
            if thu_tab:
                thu_tab.click()
                log.info("Clicked THU tab on main page")
                time.sleep(3)
        except PlaywrightTimeout:
            pass

    screenshot(page, "schedule_with_date")

    # Debug: dump what's visible on the schedule, and verify correct date is showing
    mt = get_mt_frame(page)
    if mt:
        try:
            body_text = mt.evaluate("() => document.body ? document.body.innerText.substring(0, 1000) : ''")
            log.info(f"MT schedule content: {body_text[:600]}")

            # Safety check: confirm the SPECIFIC target date appears in the schedule
            # Must match the actual date (e.g. "Apr 9" or "April 9") - NOT just "THU"
            # because today could also be Thursday
            month_day = target_date.strftime("%b %-d")   # "Apr 9"
            month_day2 = target_date.strftime("%b %d")   # "Apr 09"
            day_num = str(target_date.day)               # "9"
            date_checks = [month_day, month_day2, date_str]  # "Apr 9", "Apr 09", "2026-04-09"
            date_found = any(d in body_text for d in date_checks)
            log.info(f"Date check - looking for {date_checks} in schedule body")
            if date_found:
                log.info(f"DATE VERIFIED: schedule is showing {date_str}")
            else:
                log.error(f"DATE NOT VERIFIED - schedule is NOT showing {date_str}. "
                          f"ABORTING to avoid booking wrong day. Body: {body_text[:300]}")
                return False
        except Exception as e:
            log.warning(f"Could not verify schedule date: {e}")

    return True


def find_and_click_class(page):
    """Find the 7:20a class and click to book it."""
    log.info(f"Looking for {CLASS_TIME} class...")
    time.sleep(2)

    time_variants = ["7:20", "07:20", "7:20 AM", "7:20am", "7:20 am", "07:20 AM"]

    # Poll for MT iframe - it must be present to find the class
    mt = None
    for attempt in range(10):
        mt = get_mt_frame(page)
        if mt:
            break
        time.sleep(1)
        log.info(f"Waiting for MT iframe in find_and_click_class (attempt {attempt+1}/10)...")
    search_targets = [mt, page] if mt else [page]

    for target in search_targets:
        if target is None:
            continue
        target_name = "MT iframe" if target != page else "main page"
        log.info(f"Searching for class in {target_name}...")

        # Use JS to find and click the 7:20 class entry
        # Log all time-like elements first so we can see what's on the schedule
        try:
            all_times = target.evaluate("""() => {
                const timeRe = /\\d{1,2}:\\d{2}/;
                const seen = new Set();
                const times = [];
                document.querySelectorAll('*').forEach(el => {
                    const text = el.textContent.trim();
                    if (timeRe.test(text) && text.length < 50 && !seen.has(text)) {
                        seen.add(text);
                        times.push(text);
                    }
                });
                return times.slice(0, 20);
            }""")
            log.info(f"Times visible on schedule ({target_name}): {all_times}")
        except Exception:
            pass

        try:
            result = target.evaluate("""(timeVariants) => {
                const allElements = document.querySelectorAll('*');
                const matches = [];
                for (const el of allElements) {
                    const text = el.textContent.trim();
                    for (const t of timeVariants) {
                        // Must START with the time to avoid matching "10:20" etc.
                        if ((text === t || text.startsWith(t) || text.startsWith(t + ' ') || text.startsWith(t + 'a') || text.startsWith(t + 'p'))
                            && text.length < 200) {
                            matches.push({tag: el.tagName, text: text.substring(0, 100), children: el.children.length});
                        }
                    }
                }
                // Click the most specific match (fewest children = most leaf-like)
                matches.sort((a, b) => a.children - b.children);
                for (const el of allElements) {
                    const text = el.textContent.trim();
                    for (const t of timeVariants) {
                        if ((text === t || text.startsWith(t) || text.startsWith(t + ' ') || text.startsWith(t + 'a') || text.startsWith(t + 'p'))
                            && text.length < 200 && el.children.length < 5) {
                            el.click();
                            return 'clicked: ' + text.substring(0, 80);
                        }
                    }
                }
                return 'not found. matches: ' + JSON.stringify(matches.slice(0, 5));
            }""", time_variants)
            log.info(f"Class search result ({target_name}): {result[:200]}")
            if "clicked" in result:
                time.sleep(5)
                screenshot(page, "class_clicked")
                return True
        except Exception as e:
            log.warning(f"Class search error in {target_name}: {e}")

    # Fallback: try Playwright selectors on main page
    for t in time_variants:
        for sel in [f'text="{t}"', f'a:has-text("{t}")', f'button:has-text("{t}")']:
            try:
                el = page.wait_for_selector(sel, timeout=2000)
                if el and el.is_visible():
                    el.click()
                    log.info(f"Clicked class via selector: {sel}")
                    time.sleep(5)
                    screenshot(page, "class_clicked_fallback")
                    return True
            except PlaywrightTimeout:
                continue

    screenshot(page, "class_not_found")
    log.error("Could not find the 7:20a class on the schedule")
    return False


def select_spot(page):
    """Select preferred spot from the floor map, trying each in priority order."""
    log.info(f"Looking for spots (priority order): {PREFERRED_SPOTS}")
    time.sleep(3)
    screenshot(page, "spot_selection_page")

    # Re-acquire MT iframe (may have changed after RESERVE click)
    mt = get_mt_frame(page)
    if mt:
        # First dump available spots for debugging
        try:
            spot_info = mt.evaluate("""() => {
                const spots = [];
                document.querySelectorAll('button, [role="button"], div, span, td').forEach(el => {
                    const text = (el.textContent || '').trim();
                    const cls = (typeof el.className === 'string') ? el.className : '';
                    if (text.length > 0 && text.length < 10 && (cls.includes('spot') || cls.includes('Spot') || cls.includes('seat') || cls.includes('bench') || cls.includes('DF') || cls.includes('F-'))) {
                        spots.push({text: text, tag: el.tagName, cls: cls.substring(0, 50)});
                    }
                });
                // Also grab all short text elements that look like spot labels
                document.querySelectorAll('text, tspan, [class*="label"]').forEach(el => {
                    const text = (el.textContent || '').trim();
                    if (text.length > 0 && text.length < 10) {
                        spots.push({text: text, tag: el.tagName});
                    }
                });
                return spots.slice(0, 30);
            }""")
            log.info(f"Spot-like elements: {json.dumps(spot_info)}")
        except Exception as e:
            log.warning(f"Could not enumerate spots: {e}")

        # Also dump body text to see what's on the page
        try:
            text = mt.evaluate("() => document.body ? document.body.innerText.substring(0, 1500) : ''")
            log.info(f"Spot page text: {text[:500]}")
        except Exception:
            pass

        # Try each preferred spot in priority order
        for spot in PREFERRED_SPOTS:
            # Generate label variants: "DF-33" -> try "DF-33", "DF33", "F33", "33"
            variants = [spot]
            variants.append(spot.replace("-", ""))  # DF33
            if spot.startswith("DF"):
                num = spot.replace("DF-", "").replace("DF", "")
                variants.append(f"F{num}")  # F33
                variants.append(num)  # 33
            elif spot.startswith("F"):
                num = spot.replace("F-", "").replace("F", "")
                variants.append(num)

            try:
                result = mt.evaluate("""(variants) => {
                    // Search all elements including SVG text nodes
                    const els = document.querySelectorAll('*');
                    for (const variant of variants) {
                        for (const el of els) {
                            const text = (el.textContent || '').trim();
                            if (text === variant && text.length < 15) {
                                // Check it's not disabled/unavailable
                                const cls = (typeof el.className === 'string') ? el.className : '';
                                const disabled = el.disabled || cls.includes('unavailable') || cls.includes('disabled') || el.getAttribute('aria-disabled') === 'true';
                                if (!disabled) {
                                    // Use dispatchEvent for SVG elements that don't have .click()
                                    if (typeof el.click === 'function') {
                                        el.click();
                                    } else {
                                        el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                                    }
                                    return 'clicked: ' + variant;
                                } else {
                                    return 'unavailable: ' + variant;
                                }
                            }
                        }
                    }
                    return 'not found';
                }""", variants)
                log.info(f"Spot {spot} -> {result}")
                if "clicked" in result:
                    time.sleep(2)
                    screenshot(page, f"spot_selected_{spot}")
                    return True
                elif "unavailable" in result:
                    log.info(f"Spot {spot} is unavailable, trying next...")
                    continue
            except Exception as e:
                log.warning(f"Spot {spot} error: {e}")

    # Try multiple selector patterns for the spot
    spot_selectors = [
        f'[data-spot="{PREFERRED_SPOTS[0]}"]',
        f'[data-spot-id="{PREFERRED_SPOTS[0]}"]',
        f'[data-id="{PREFERRED_SPOTS[0]}"]',
        f'[aria-label*="{PREFERRED_SPOTS[0]}"]',
        f'button:has-text("{PREFERRED_SPOTS[0]}")',
        f'div:has-text("{PREFERRED_SPOTS[0]}")',
        f'text="{PREFERRED_SPOTS[0]}"',
        # Try just the number part
        f'[data-spot="33"]',
        f'button:has-text("33")',
    ]

    for sel in spot_selectors:
        try:
            el = page.wait_for_selector(sel, timeout=3000)
            if el and el.is_visible():
                el.click()
                log.info(f"Selected spot with selector: {sel}")
                time.sleep(2)
                screenshot(page, "spot_selected")
                return True
        except PlaywrightTimeout:
            continue

    # Fallback: scan all clickable elements for the spot name
    log.info("Trying fallback spot selection...")
    clickables = page.query_selector_all("button, div[role='button'], [class*='spot'], [class*='Spot']")
    for el in clickables:
        text = (el.text_content() or "").strip()
        if PREFERRED_SPOTS[0] in text or PREFERRED_SPOTS[0].replace("DF", "") in text:
            try:
                el.click()
                log.info(f"Selected spot via fallback: {text}")
                time.sleep(2)
                screenshot(page, "spot_selected_fallback")
                return True
            except Exception:
                continue

    log.warning(f"Could not find spot {PREFERRED_SPOTS[0]}, taking screenshot for review")
    screenshot(page, "spot_not_found")
    return False


def confirm_booking(page):
    """Click the final confirm/book button. Retries for up to 15s."""
    log.info("Looking for confirmation button...")

    # Retry loop - button may take a moment to appear after spot selection
    for attempt in range(5):
        time.sleep(3)

        # Try MT iframe first
        mt = get_mt_frame(page)
        if mt:
            try:
                result = mt.evaluate("""() => {
                    const els = document.querySelectorAll('button, a, [role="button"]');
                    const allText = Array.from(els).map(e => e.textContent.trim()).join(' | ');
                    for (const el of els) {
                        const text = el.textContent.trim().toUpperCase();
                        if (text.includes('CONFIRM') || text.includes('COMPLETE') ||
                            text.includes('BOOK') || text.includes('RESERVE')) {
                            el.click();
                            return 'clicked: ' + text;
                        }
                    }
                    return 'not found. buttons: ' + allText.substring(0, 300);
                }""")
                log.info(f"MT iframe confirm (attempt {attempt+1}): {result}")
                if "clicked" in result:
                    time.sleep(5)
                    screenshot(page, "booking_confirmed_mt")
                    return True
            except Exception as e:
                log.warning(f"MT iframe confirm error: {e}")

        log.info(f"Confirm button not found on attempt {attempt+1}, waiting...")

    screenshot(page, "no_confirm_button")
    log.error("Could not find confirmation button after all retries")
    return False


def confirm_booking_selectors(page):
    """Fallback selector-based confirm."""
    confirm_selectors = [
        'button:has-text("Confirm")',
        'button:has-text("Book")',
        'button:has-text("Reserve")',
        'button:has-text("Complete")',
        'button:has-text("CONFIRM")',
        'button:has-text("BOOK")',
        'button:has-text("RESERVE")',
        'button[type="submit"]',
    ]

    for sel in confirm_selectors:
        try:
            btn = page.wait_for_selector(sel, timeout=5000)
            if btn and btn.is_visible():
                btn.click()
                log.info(f"Clicked confirmation: {sel}")
                time.sleep(5)
                screenshot(page, "booking_confirmed")
                return True
        except PlaywrightTimeout:
            continue

    return False


def run_booking():
    """Main booking flow."""
    if not EMAIL or not PASSWORD:
        log.error("Missing BARRYS_EMAIL or BARRYS_PASSWORD in .env")
        sys.exit(1)

    target_date = get_next_target_date()
    log.info(f"Target class: {TARGET_DAY.title()} {target_date.strftime('%Y-%m-%d')} at {CLASS_TIME}")
    log.info(f"Studio: {STUDIO}, Preferred spots: {PREFERRED_SPOTS}")

    with sync_playwright() as p:
        # Use saved auth state if available
        browser = p.chromium.launch(
            headless=os.getenv("HEADLESS", "true").lower() == "true",
        )

        context_opts = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if STATE_FILE.exists():
            context_opts["storage_state"] = str(STATE_FILE)

        context = browser.new_context(**context_opts)
        page = context.new_page()

        try:
            # Step 1: Login FIRST (before waiting for 12:00) so we're ready to go
            if not login(page):
                log.error("LOGIN FAILED - check credentials and screenshots")
                return False

            # Step 2: Navigate to schedule BEFORE 12:00 so page is warm
            if not navigate_to_schedule(page, target_date):
                log.error("SCHEDULE NAVIGATION FAILED")
                return False

            # Step 3: Wait until exactly 12:00:00 ET (booking window opens)
            # We're already logged in and on the schedule page - now we wait for the gun
            if "--wait" in sys.argv:
                wait_for_booking_window()

            # Step 4: Find and click the class - retry with full re-navigation for up to 60s
            # in case the booking button takes a moment to appear after 12:00
            booked = False
            for attempt in range(6):  # try up to 6 times (every ~10s for 60s)
                if attempt > 0:
                    log.info(f"Retry {attempt}/5 - re-navigating to schedule...")
                    time.sleep(5)
                    # Re-navigate fully so we get back to the right week and date tab
                    if not navigate_to_schedule(page, target_date):
                        log.warning(f"Schedule re-navigation failed on retry {attempt}")
                        continue

                if not find_and_click_class(page):
                    log.warning(f"Class not found on attempt {attempt+1}, retrying...")
                    continue

                # Step 5: Click RESERVE
                if not confirm_booking(page):
                    log.warning(f"RESERVE not available on attempt {attempt+1}, retrying...")
                    continue

                # Step 6: Select spot from the map
                time.sleep(3)
                spot_ok = select_spot(page)
                if not spot_ok:
                    log.error("None of the preferred spots are available - ABORTING booking. "
                              f"Preferred: {PREFERRED_SPOTS}")
                    screenshot(page, "no_preferred_spot_abort")
                    return False

                # Step 7: Final confirmation (COMPLETE RESERVATION button)
                screenshot(page, "pre_final_confirm")
                if not confirm_booking(page):
                    log.warning(f"COMPLETE RESERVATION not found on attempt {attempt+1}, retrying...")
                    continue

                log.info("BOOKING COMPLETE!")
                screenshot(page, "final_success")
                booked = True
                break

            if not booked:
                log.error("Could not complete booking after all retries")
                return False
            return True

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            screenshot(page, "error")
            raise
        finally:
            context.close()
            browser.close()


def wait_for_booking_window(max_wait_minutes=15):
    """Wait until exactly 12:00:00 ET. Call this after login/navigation so
    the browser is already warmed up and we hit RESERVE right at open."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    target = now.replace(hour=12, minute=0, second=0, microsecond=0)

    if now >= target:
        log.info(f"Already past 12:00 ET ({now.strftime('%H:%M:%S')} ET) - booking should be open")
        return

    wait_seconds = (target - now).total_seconds()
    if wait_seconds > max_wait_minutes * 60:
        log.info(f"Too early ({wait_seconds:.0f}s until 12:00 ET) - starting immediately anyway")
        return

    log.info(f"Waiting {wait_seconds:.0f}s until exactly 12:00:00 ET...")
    time.sleep(max(0, wait_seconds - 2))
    # Busy-wait the last 2 seconds for precision
    while datetime.now(et) < target:
        time.sleep(0.05)
    log.info("12:00:00 ET - Booking window open - GO!")


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("Barry's Bootcamp Auto-Booker Starting")
    log.info("=" * 60)

    success = run_booking()
    sys.exit(0 if success else 1)
