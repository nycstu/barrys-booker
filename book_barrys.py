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
    # Find the Mariana Tek iframe frame
    mt_frame = None
    for frame in page.frames:
        if "marianaiframes.com" in frame.url:
            mt_frame = frame
            log.info(f"Found Mariana Tek iframe: {frame.url[:80]}")
            break

    if not mt_frame:
        log.error("Could not find Mariana Tek iframe")
        screenshot(page, "no_mt_iframe")
        return False

    # Wait for the iframe SPA to fully render
    login_frame = mt_frame
    log.info("Waiting for Mariana Tek iframe to render...")
    time.sleep(5)
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

    # Re-acquire the MT iframe frame reference (it navigated to the login page)
    login_frame = None
    for frame in page.frames:
        if "marianaiframes.com" in frame.url:
            login_frame = frame
            log.info(f"Re-acquired MT iframe after navigation: {frame.url[:80]}")
            break
    if not login_frame:
        log.error("Lost MT iframe after LOG IN click")
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

    # Verify login succeeded by checking for account content in the MT iframe
    logged_in = False
    for frame in page.frames:
        if "marianaiframes.com" in frame.url:
            try:
                body_text = frame.evaluate("() => document.body ? document.body.innerText.substring(0, 300) : ''")
                if "Log Out" in body_text or "Reservations" in body_text or "Account" in body_text:
                    logged_in = True
                    log.info("Login verified - found account content in MT iframe")
                    break
            except Exception:
                pass
    if not logged_in:
        log.error("Login may have failed - no account content found")
        return False

    # Save auth state for future runs
    page.context.storage_state(path=str(STATE_FILE))
    log.info("Login successful, auth state saved")
    return True


def get_mt_frame(page):
    """Find and return the current Mariana Tek iframe."""
    for frame in page.frames:
        if "marianaiframes.com" in frame.url or "marianatek.com" in frame.url:
            return frame
    return None


def navigate_to_schedule(page, target_date):
    """Navigate to the Noho schedule for the target date."""
    date_str = target_date.strftime("%Y-%m-%d")
    day_name = target_date.strftime("%a").upper()  # THU
    log.info(f"Navigating to schedule for {date_str} ({day_name})")

    # Navigate to schedule page
    try:
        page.goto(SCHEDULE_URL, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeout:
        log.warning("Schedule page load timed out, continuing...")
    time.sleep(5)
    screenshot(page, "schedule_page")

    # The schedule is inside the MT iframe. Find it and click the right date tab.
    mt = get_mt_frame(page)
    if mt:
        log.info("Found MT iframe on schedule page, looking for date tabs...")
        time.sleep(3)

        # Click the Thursday date tab inside the MT iframe
        # The tabs look like "Apr 2 THU" or just "THU" or the date number
        day_short = target_date.strftime("%a")  # "Thu"
        day_num = target_date.day
        month_short = target_date.strftime("%b")  # "Apr"

        clicked_date = mt.evaluate(f"""() => {{
            const els = document.querySelectorAll('*');
            for (const el of els) {{
                const text = el.textContent.trim();
                // Match "THU" tab or "Apr 2" or date number
                if ((text.includes('THU') || text.includes('Thu')) && text.length < 20) {{
                    el.click();
                    return 'clicked: ' + text;
                }}
            }}
            return 'not found';
        }}""")
        log.info(f"Date tab click: {clicked_date}")
        time.sleep(3)
    else:
        # No MT iframe on schedule page, try clicking date tabs on main page
        log.info("No MT iframe found, trying main page date navigation...")
        # Click the THU tab on the main page
        try:
            thu_tab = page.wait_for_selector(f'text=THU', timeout=5000)
            if thu_tab:
                thu_tab.click()
                log.info("Clicked THU tab on main page")
                time.sleep(3)
        except PlaywrightTimeout:
            pass

    screenshot(page, "schedule_with_date")

    # Debug: dump what's visible on the schedule
    mt = get_mt_frame(page)
    if mt:
        try:
            body_text = mt.evaluate("() => document.body ? document.body.innerText.substring(0, 1000) : ''")
            log.info(f"MT schedule content: {body_text[:500]}")
        except Exception:
            pass

    return True


def find_and_click_class(page):
    """Find the 7:20a class and click to book it."""
    log.info(f"Looking for {CLASS_TIME} class...")
    time.sleep(2)

    time_variants = ["7:20", "07:20", "7:20 AM", "7:20am", "7:20 am", "07:20 AM"]

    # The classes are inside the MT iframe
    mt = get_mt_frame(page)
    search_targets = [mt, page] if mt else [page]

    for target in search_targets:
        if target is None:
            continue
        target_name = "MT iframe" if target != page else "main page"
        log.info(f"Searching for class in {target_name}...")

        # Use JS to find and click the 7:20 class entry
        try:
            result = target.evaluate("""(timeVariants) => {
                const allElements = document.querySelectorAll('*');
                const matches = [];
                for (const el of allElements) {
                    const text = el.textContent.trim();
                    for (const t of timeVariants) {
                        if (text.includes(t) && text.length < 200) {
                            matches.push({tag: el.tagName, text: text.substring(0, 100), children: el.children.length});
                        }
                    }
                }
                // Click the most specific match (fewest children = most leaf-like)
                matches.sort((a, b) => a.children - b.children);
                for (const el of allElements) {
                    const text = el.textContent.trim();
                    for (const t of timeVariants) {
                        if (text.includes(t) && text.length < 200 && el.children.length < 5) {
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
    """Click the final confirm/book button."""
    log.info("Looking for confirmation button...")
    time.sleep(2)

    # Try MT iframe first
    mt = get_mt_frame(page)
    if mt:
        try:
            result = mt.evaluate("""() => {
                const els = document.querySelectorAll('button, a, [role="button"]');
                for (const el of els) {
                    const text = el.textContent.trim().toUpperCase();
                    if (text.includes('CONFIRM') || text.includes('BOOK') || text.includes('RESERVE') || text.includes('COMPLETE')) {
                        el.click();
                        return 'clicked: ' + text;
                    }
                }
                return 'not found';
            }""")
            log.info(f"MT iframe confirm: {result}")
            if "clicked" in result:
                time.sleep(5)
                screenshot(page, "booking_confirmed_mt")
                return True
        except Exception as e:
            log.warning(f"MT iframe confirm error: {e}")

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

    screenshot(page, "no_confirm_button")
    log.error("Could not find confirmation button")
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
        }
        if STATE_FILE.exists():
            context_opts["storage_state"] = str(STATE_FILE)

        context = browser.new_context(**context_opts)
        page = context.new_page()

        try:
            # Step 1: Login
            if not login(page):
                log.error("LOGIN FAILED - check credentials and screenshots")
                return False

            # Step 2: Navigate to schedule
            if not navigate_to_schedule(page, target_date):
                log.error("SCHEDULE NAVIGATION FAILED")
                return False

            # Step 3: Find and click the class
            if not find_and_click_class(page):
                log.error("CLASS NOT FOUND - check if booking window is open")
                return False

            # Step 4: Click RESERVE to get to spot selection
            # (Barry's flow: click class -> RESERVE -> spot map -> confirm)
            if not confirm_booking(page):
                log.error("RESERVE BUTTON NOT FOUND")
                return False

            # Step 5: Select spot from the map
            time.sleep(3)
            spot_ok = select_spot(page)
            if not spot_ok:
                log.warning(f"No preferred spot available, continuing without spot selection...")

            # Step 6: Final confirmation (if there's another confirm button after spot)
            screenshot(page, "pre_final_confirm")
            confirm_booking(page)  # May or may not have another confirm

            log.info("BOOKING COMPLETE!")
            screenshot(page, "final_success")
            return True

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            screenshot(page, "error")
            raise
        finally:
            context.close()
            browser.close()


def wait_for_booking_window():
    """If running early, wait until exactly 12:00:00 ET to start."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    target = now.replace(hour=12, minute=0, second=0, microsecond=0)

    if now < target:
        wait_seconds = (target - now).total_seconds()
        if wait_seconds < 300:  # only wait if less than 5 min early
            log.info(f"Waiting {wait_seconds:.0f}s until 12:00:00 ET...")
            time.sleep(max(0, wait_seconds - 2))
            # Busy-wait the last 2 seconds for precision
            while datetime.now(et) < target:
                time.sleep(0.05)
            log.info("Booking window open - GO!")


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("Barry's Bootcamp Auto-Booker Starting")
    log.info("=" * 60)

    # If --wait flag passed, wait for the 12p window
    if "--wait" in sys.argv:
        wait_for_booking_window()

    success = run_booking()
    sys.exit(0 if success else 1)
