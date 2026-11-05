"""
SunoClient - talks to Suno's internal web API directly (no browser).

Auth model (reverse-engineered 2026-11-02):
  - Base:     https://suno.com/a/api/...
  - Header:   Authorization: Bearer <JWT>   (the Clerk __session cookie)
  - Cookies:  all suno.com cookies from auth_state.json
  - JWT TTL:  ~1 h. refresh() asks Clerk (auth.suno.com) for a fresh
              __session with the long-lived __client cookie from
              auth_state.json - no browser needed, so it also works in a
              background service and on a PC the Chrome profile was not made
              on. If that fails it re-opens the saved Chrome profile headless,
              lets Clerk mint a fresh __session, and updates auth_state.json.
              Works silently as long as the Clerk session is alive; otherwise
              run login.py again.
"""
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
STATE_FILE = HERE / "auth_state.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
DEFAULT_MODEL = "chirp-goose"   # v6-mini
CLERK = "https://auth.suno.com"
CLERK_JS = "5.56.0"


def chrome_path() -> str:
    """The installed Google Chrome (SUNO_CHROME overrides it). Real Chrome, because Google
    refuses sign-ins from automation browsers."""
    env = os.environ.get("SUNO_CHROME", "").strip()
    if env:
        return env
    if sys.platform == "win32":
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return "/usr/bin/google-chrome"


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
    except Exception:
        return 0


class SunoError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status   # Suno's HTTP status when it refused (0: not an HTTP refusal)


class SunoClient:
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = Path(state_file)
        self._token = None
        self._cli = None
        # The server answers requests on several threads: one refresh at a time.
        self._lock = threading.RLock()

    # ---------- auth ----------

    def _load_state(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def token(self) -> str:
        if self._token and _jwt_exp(self._token) > time.time() + 120:
            return self._token
        state = self._load_state()
        for c in state.get("cookies", []):
            if c["name"] == "__session" and len(c["value"]) > 500:
                self._token = c["value"]
                return self._token
        raise SunoError("no __session token in auth_state.json - run login.py")

    def is_token_expired(self) -> bool:
        return _jwt_exp(self.token()) < time.time() + 120

    def refresh(self) -> str:
        """Re-mint the JWT: through Clerk's API first, else the Chrome profile."""
        with self._lock:
            # Another thread may have refreshed while this one waited.
            if self._token and _jwt_exp(self._token) > time.time() + 120:
                return self._token
            try:
                return self._refresh_via_clerk()
            except Exception as e:          # noqa: BLE001 - fall back to Chrome
                clerk_error = e
            try:
                return self._refresh_via_chrome()
            except Exception as e:          # noqa: BLE001
                raise SunoError(f"refresh failed (Clerk: {clerk_error}; Chrome: {e}) "
                                "- the session may be dead; run login.py") from e

    def _refresh_via_clerk(self) -> str:
        """Clerk's frontend API: the active session's new token from the __client cookie."""
        state = self._load_state()
        jar = {c["name"]: c["value"] for c in state.get("cookies", [])
               if c["domain"].lstrip(".") in ("auth.suno.com", "suno.com")
               and c["name"].startswith("__client")}
        if "__client" not in jar:
            raise SunoError("no __client cookie in auth_state.json")
        headers = {"user-agent": UA, "origin": "https://suno.com",
                   "referer": "https://suno.com/",
                   "cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}
        with httpx.Client(timeout=30, headers=headers) as h:
            r = h.get(f"{CLERK}/v1/client", params={"_clerk_js_version": CLERK_JS})
            r.raise_for_status()
            resp = r.json().get("response") or {}
            sid = resp.get("last_active_session_id") or next(
                (s["id"] for s in resp.get("sessions", []) if s.get("status") == "active"), None)
            if not sid:
                raise SunoError("Clerk has no active session")
            rotated = dict(r.cookies.items())
            r = h.post(f"{CLERK}/v1/client/sessions/{sid}/tokens",
                       params={"_clerk_js_version": CLERK_JS})
            r.raise_for_status()
            rotated.update(r.cookies.items())
            jwt = r.json().get("jwt") or ""
        if len(jwt) < 500:
            raise SunoError("Clerk returned no session token")
        self._save_session(jwt, {k: v for k, v in rotated.items() if k.startswith("__client")})
        return jwt

    def _save_session(self, jwt: str, client_cookies: dict) -> None:
        """Writes the new __session (and any rotated __client) into auth_state.json."""
        state = self._load_state()
        exp = _jwt_exp(jwt)
        for c in state.get("cookies", []):
            if c["name"] == "__session" or c["name"].startswith("__session_"):
                c["value"] = jwt
                if exp:
                    c["expires"] = exp
            elif c["name"] in client_cookies and c["domain"].lstrip(".") == "auth.suno.com":
                c["value"] = client_cookies[c["name"]]
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.state_file)
        self._token = jwt
        self._cli = None        # rebuilt with the new cookies

    def _refresh_via_chrome(self) -> str:
        """Re-mint the JWT by opening the saved Chrome profile headlessly."""
        from playwright.sync_api import sync_playwright
        profile = HERE / "chrome-profile"
        chrome = chrome_path()
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=chrome, headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://suno.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            ctx.storage_state(path=str(self.state_file))
            cookies = ctx.cookies("https://suno.com")
            ctx.close()
        self._cli = None
        for c in cookies:
            if c["name"] == "__session" and len(c["value"]) > 500:
                self._token = c["value"]
                return self._token
        raise SunoError("refresh failed - Clerk session may be dead; run login.py")

    def _client(self) -> httpx.Client:
        # rebuild if token changed (picks up new cookie jar)
        if self._cli is None:
            state = self._load_state()
            jar = httpx.Cookies()
            for c in state.get("cookies", []):
                if "suno" in c["domain"]:
                    jar.set(c["name"], c["value"], domain=c["domain"].lstrip("."),
                            path=c.get("path", "/"))
            self._cli = httpx.Client(
                base_url="https://suno.com", cookies=jar, timeout=90,
                headers={"user-agent": UA, "accept": "application/json",
                         "origin": "https://suno.com", "referer": "https://suno.com/"})
        self._cli.headers["authorization"] = f"Bearer {self.token()}"
        return self._cli

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        cli = self._client()
        if self.is_token_expired():
            self.refresh()
            cli = self._client()
        r = cli.request(method, path, **kw)
        if r.status_code == 401:            # one retry with a fresh token
            self._token = None
            self.refresh()
            cli = self._client()
            r = cli.request(method, path, **kw)
        return r

    # ---------- account ----------

    def me(self) -> dict:
        r = self._req("GET", "/a/api/session/")
        r.raise_for_status()
        return r.json().get("user", {})

    def billing(self) -> dict:
        r = self._req("GET", "/a/api/billing/info/")
        r.raise_for_status()
        b = r.json()
        u = b.get("download_usage") or {}
        b["downloads_left"] = max((u.get("current_period_downloads_limit") or 0)
                                  - (u.get("current_period_downloads_used") or 0), 0) \
            + (u.get("additional_download_remaining") or 0)
        b["available_credits"] = max((b.get("monthly_limit") or 0)
                                     - (b.get("monthly_usage") or 0), 0) \
            + (b.get("credits") or 0)
        return b

    def pending_batches(self) -> list:
        r = self._req("POST", "/a/api/video_gen/pending_batches", json={})
        return r.json().get("batch_ids", []) if r.status_code == 200 else []

    # ---------- generation ----------

    def generate(self, prompt: str, tags: str = "", title: str = "",
                 lyrics: str = "", instrumental: bool = False,
                 model: str = DEFAULT_MODEL, **extra) -> dict:
        """
        POST /a/api/generate/v2/   (flat payload, verified working).
        prompt = style/idea description; lyrics = custom lyrics (optional).
        Returns the raw response: {"id": <batch_id>, "clips": [...] }.
        """
        if not prompt and not lyrics:
            raise ValueError("prompt or lyrics required")
        payload = {"prompt": prompt, "tags": tags, "title": title,
                   "lyrics": lyrics, "make_instrumental": instrumental,
                   "model": model, "stream": True}
        payload.update(extra)
        r = self._req("POST", "/a/api/generate/v2/", json=payload)
        if r.status_code != 200:
            raise SunoError(f"generate failed {r.status_code}: {r.text[:500]}")
        return r.json()

    def generate_lyrics(self, instruction: str) -> dict:
        """AI lyric writing (captured from the real UI)."""
        payload = {"selected": "", "context_before": "", "context_after": "",
                   "instruction": instruction, "title": "", "style": "",
                   "mode": "apply_user_request", "references": [],
                   "num_variants": None, "lyricist_id": None,
                   "metadata": {"lyrics_model": "default", "enable_thinking": False},
                   "create_session_token": None, "lyrics_project_id": None}
        r = self._req("POST", "/a/api/generate/cowrite-lyrics/", json=payload)
        if r.status_code != 200:
            raise SunoError(f"lyrics failed {r.status_code}: {r.text[:300]}")
        return r.json()

    # ---------- results ----------

    def feed(self, limit: int = 20) -> list:
        body = {"cursor": None, "limit": limit,
                "filters": {"disliked": "False", "trashed": "False",
                            "fromStudioProject": {"presence": "False"},
                            "stem": {"presence": "False"},
                            "stemComplement": "False",
                            "workspace": {"presence": "True",
                                          "workspaceId": "default"}}}
        r = self._req("POST", "/a/api/feed/v3", json=body)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("clips", [])

    def wait_for_clips(self, clip_ids, timeout_s: int = 300,
                       poll_s: int = 10) -> list:
        """Poll feed/v3 until all given clips are complete (or error)."""
        ids = {c for c in clip_ids}
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            clips = {c["id"]: c for c in self.feed(limit=25)}
            ours = [clips[i] for i in ids if i in clips]
            if ours and all(c.get("status") in ("complete", "error", "failed")
                            for c in ours):
                return ours
            time.sleep(poll_s)
        raise TimeoutError(f"clips {ids} not finished in {timeout_s}s")

    def audio_url(self, clip: dict) -> str:
        """Real audio lives in media_urls (audio_url is a placeholder)."""
        mu = clip.get("media_urls") or []
        if mu:
            return mu[0]["url"]
        return clip.get("audio_url") or ""

    def download_audio(self, clip_id: str, out_file: Path) -> Path:
        clip = self.clip(clip_id)
        url = self.audio_url(clip)
        if not url or "forbidden" in url:
            raise SunoError("no downloadable audio for this clip (not complete?)")
        # Suno scrambles the stream its own player plays ("encoding" in media_urls): only an
        # official download (download_file) is a file other players can play.
        media = (clip.get("media_urls") or [{}])[0]
        if media.get("encoding"):
            raise SunoError("Suno's stream of this song is scrambled for its own player: "
                            "use GET /clip/{id}/download (an official download)")
        r = httpx.get(url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        out_file = Path(out_file)
        out_file.write_bytes(r.content)
        return out_file

    def download_url(self, clip_id: str, fmt: str = "mp3", timeout_s: int = 180) -> str:
        """Suno's official download (what the Download button on suno.com does): a signed link
        to a playable file. The first download of a song uses one of the account's downloads
        (billing()["downloads_left"]); later ones of the same song are free."""
        # Like suno.com: a song not unlocked yet is authorized first, which uses one download.
        if not self.clip(clip_id).get("is_download_unlocked"):
            a = self._req("POST", "/a/api/download/authorize", json={"item_id": clip_id, "item_type": "clip"})
            try:
                d = a.json()
            except ValueError:
                d = {}
            if not isinstance(d, dict):
                d = {}
            if a.status_code >= 400 or not d.get("ok"):
                why = d.get("message") or d.get("detail") or d.get("reason") or a.text[:300]
                quota = d.get("reason") == "quota_exhausted" or a.status_code in (402, 403)
                raise SunoError(f"Suno did not authorize the download: {why}", status=402 if quota else a.status_code)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = self._req("GET", f"/a/api/download/clip/{clip_id}", params={"format": fmt})
            try:
                d = r.json()
            except ValueError:
                d = {}
            if not isinstance(d, dict):
                d = {}
            if r.status_code >= 400:
                detail = d.get("detail") or d.get("message") or r.text[:300]
                raise SunoError(f"Suno refused the download ({r.status_code}): {detail}",
                                status=r.status_code)
            if d.get("reason") == "rate_limited":
                time.sleep(5)
                continue
            if not d.get("ok"):
                raise SunoError(d.get("detail") or d.get("message") or "Suno gave no download link.")
            if d.get("status") == "ready":
                if not d.get("download_url"):
                    raise SunoError("Suno's download link is missing.")
                return d["download_url"]
            if d.get("status") == "error":
                raise SunoError(d.get("detail") or d.get("message") or "Suno could not prepare the download.")
            time.sleep(2)
        raise SunoError("Suno took too long to prepare the download.")

    def download_file(self, clip_id: str, out_file: Path, fmt: str = "mp3") -> Path:
        """A playable file of the song through Suno's official download (see download_url)."""
        url = self.download_url(clip_id, fmt)
        r = httpx.get(url, timeout=180, follow_redirects=True)
        r.raise_for_status()
        out_file = Path(out_file)
        out_file.write_bytes(r.content)
        return out_file

    def clip(self, clip_id: str) -> dict:
        for c in self.feed(limit=50):
            if c["id"] == clip_id:
                return c
        raise SunoError(f"clip {clip_id} not in recent feed")


if __name__ == "__main__":
    sc = SunoClient()
    print("user:   ", sc.me().get("display_name"))
    b = sc.billing()
    print("credits:", b["available_credits"], "available")
    print("pending:", sc.pending_batches())
    latest = sc.feed(limit=3)
    for c in latest:
        print(f"clip: {c.get('title')!r:30} {c.get('status'):9} {c.get('id')}")
