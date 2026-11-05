"""
One-time login: opens your REAL Chrome (so Google doesn't block the sign-in),
you sign in to suno.com with your Google account, and the session is saved to
auth_state.json for all future scripted requests.

Uses a dedicated profile folder (chrome-profile/) - not your daily Chrome data.
The window closes BY ITSELF once a real session cookie appears.

Run:  python login.py

SUNO_CHROME sets the Chrome executable when it is not in the usual place.
"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from suno_client import chrome_path

HERE = Path(__file__).parent
STATE_FILE = HERE / "auth_state.json"
PROFILE = HERE / "chrome-profile"
CHROME = chrome_path()
START_URL = "https://suno.com/"
TIMEOUT_MIN = 15


def session_cookie(context):
    """A strong signal: a cookie that only exists when you're logged in."""
    for c in context.cookies("https://suno.com"):
        n = c["name"].lower()
        v = c["value"]
        if n in ("__session", "session", "suno_session") and len(v) > 100:
            return c
        if ("session" in n or "jwt" in n or "auth" in n or "token" in n) and len(v) > 150:
            return c
    return None


def main() -> None:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            executable_path=CHROME,
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--no-default-browser-check"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(START_URL)

        print("=" * 70)
        print("Real Chrome is open. Sign in to Suno with your Google account.")
        print(f"Polling for a session cookie ({TIMEOUT_MIN} min max).")
        print("The window closes BY ITSELF once you're logged in.")
        print("=" * 70, flush=True)

        deadline = time.time() + TIMEOUT_MIN * 60
        hits = 0
        cookie = None
        seen = set()
        while time.time() < deadline:
            cur = {c["name"] for c in context.cookies("https://suno.com")}
            if cur - seen:
                seen |= cur
                print("cookies now:", ", ".join(sorted(cur)), flush=True)
            cookie = session_cookie(context)
            hits = hits + 1 if cookie else 0
            if hits >= 2:
                break
            time.sleep(3)

        time.sleep(3)
        context.storage_state(path=str(STATE_FILE))
        n = len(context.cookies("https://suno.com"))
        if cookie:
            print(f"DETECTED session cookie: {cookie['name']} (len={len(cookie['value'])})")
        context.close()

    print(f"SAVED: {STATE_FILE} ({n} cookies on suno.com)")
    if not cookie:
        print("WARNING: no session cookie detected - login probably not completed.")


if __name__ == "__main__":
    main()
