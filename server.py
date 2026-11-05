"""
FastAPI wrapper around the Suno internal API.

Run:   python server.py        (http://127.0.0.1:8000 , docs at /docs)

Endpoints:
  GET  /me                    account info
  GET  /credits               available credits + pending queue
  POST /generate              {prompt, tags?, title?, lyrics?, instrumental?, model?}
                              -> batch id + clip ids (submitted)
  GET  /clips                 recent library clips
  GET  /clip/{id}             one clip (status, audio url, metadata)
  GET  /clip/{id}/audio       download the finished m4a (409 when Suno scrambles its stream)
  GET  /clip/{id}/download    Suno's official download (?format=mp3|m4a): a playable file;
                              the first one of a song uses one of the account's downloads
  GET  /wait/{batch_id}       block until that batch's clips are done
  POST /lyrics                {instruction} -> AI-written lyrics
  GET  /health                is the session usable (no credits, one cheap call)
  GET  /clips/status?ids=a,b  several clips at once (status, audio, cover, lyrics)

Environment: SUNO_HOST (127.0.0.1), SUNO_PORT (8000).
API key: every request but /health and /docs needs X-API-Key (or Authorization:
Bearer) with the key in api_key.txt, made on first start; SUNO_API_KEY overrides
it, and SUNO_API_KEY=off turns the check off.
"""
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from suno_client import SunoClient, SunoError, _jwt_exp

app = FastAPI(title="Suno API wrapper", version="1.2")
sc = SunoClient()
OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/docs/oauth2-redirect"}


def _api_key() -> str:
    """The key callers must send: SUNO_API_KEY, else api_key.txt (made on first start)."""
    env = os.environ.get("SUNO_API_KEY", "").strip()
    if env:
        return "" if env.lower() == "off" else env
    f = Path(__file__).parent / "api_key.txt"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()


API_KEY = _api_key()


@app.middleware("http")
async def require_key(request: Request, call_next):
    if API_KEY and request.url.path not in OPEN_PATHS:
        given = request.headers.get("x-api-key") or \
            request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if given != API_KEY:
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
    return await call_next(request)


def _clip_info(c: dict) -> dict:
    """Everything a caller needs about one clip."""
    md = c.get("metadata") or {}
    return {"id": c["id"], "title": c.get("title"), "status": c.get("status"),
            "audio_url": sc.audio_url(c), "image_url": c.get("image_url"),
            "image_large_url": c.get("image_large_url"),
            "duration": md.get("duration"), "tags": md.get("tags"),
            "lyrics": md.get("prompt"), "description": md.get("gpt_description_prompt"),
            "instrumental": md.get("make_instrumental"), "model": c.get("model_name"),
            "model_version": c.get("major_model_version"), "created_at": c.get("created_at"),
            "download_unlocked": c.get("is_download_unlocked") is True,
            "error": md.get("error_message") or md.get("error_type")}


class GenerateBody(BaseModel):
    prompt: str = Field("", description="style/idea description")
    tags: str = Field("", description="style tags, comma separated")
    title: str = ""
    lyrics: str = Field("", description="custom lyrics (optional)")
    instrumental: bool = False
    model: str = "chirp-goose"
    wait: bool = Field(False, description="block until clips are complete")
    timeout_s: int = Field(300, ge=30, le=900)


class LyricsBody(BaseModel):
    instruction: str


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except SunoError as e:
        raise HTTPException(502, str(e))
    except TimeoutError as e:
        raise HTTPException(504, str(e))


@app.get("/me")
def me():
    return _guard(sc.me)


@app.get("/credits")
def credits():
    b = _guard(sc.billing)
    return {"available_credits": b["available_credits"],
            "monthly_usage": b.get("monthly_usage"),
            "monthly_limit": b.get("monthly_limit"),
            "downloads_left": b.get("downloads_left"),
            "pending_batches": _guard(sc.pending_batches)}


@app.post("/generate")
def generate(body: GenerateBody):
    data = _guard(sc.generate, prompt=body.prompt, tags=body.tags,
                  title=body.title, lyrics=body.lyrics,
                  instrumental=body.instrumental, model=body.model)
    clips = data.get("clips", [])
    out = {"batch_id": data.get("id"),
           "clip_ids": [c["id"] for c in clips],
           "status": [c.get("status") for c in clips]}
    if body.wait and out["clip_ids"]:
        done = _guard(sc.wait_for_clips, out["clip_ids"], body.timeout_s)
        out["clips"] = [{"id": c["id"], "title": c.get("title"),
                         "status": c.get("status"),
                         "audio_url": sc.audio_url(c),
                         "duration": (c.get("metadata") or {}).get("duration")}
                        for c in done]
    return out


@app.get("/clips")
def clips(limit: int = 20):
    return _guard(sc.feed, limit)


@app.get("/health")
def health():
    """Whether the saved session can be used (refreshes the token when it is old)."""
    try:
        token = sc.token()
        if _jwt_exp(token) < time.time() + 120:
            token = sc.refresh()
        return {"ok": True, "token_expires_in": int(_jwt_exp(token) - time.time())}
    except SunoError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.get("/clips/status")
def clips_status(ids: str, limit: int = 50):
    """Several clips by id in one feed read (a larger read when some are older)."""
    want = [i for i in ids.split(",") if i.strip()]
    if not want:
        raise HTTPException(400, "ids is a comma separated list of clip ids")
    found = {}
    for n in (min(max(limit, 10), 100), 100):
        found = {c["id"]: c for c in _guard(sc.feed, n)}
        if all(i in found for i in want):
            break
    return [_clip_info(found[i]) if i in found else {"id": i, "status": "missing"} for i in want]


@app.get("/clip/{clip_id}")
def clip(clip_id: str):
    for c in _guard(sc.feed, 50):
        if c["id"] == clip_id:
            return _clip_info(c)
    raise HTTPException(404, "clip not found in recent feed")


@app.get("/clip/{clip_id}/audio")
def clip_audio(clip_id: str):
    import tempfile
    try:
        out = Path(tempfile.gettempdir()) / f"suno_{clip_id}.m4a"
        sc.download_audio(clip_id, out)
        return FileResponse(out, media_type="audio/mp4",
                            filename=f"suno_{clip_id}.m4a")
    except SunoError as e:
        raise HTTPException(409, str(e))


@app.get("/clip/{clip_id}/download")
def clip_download(clip_id: str, format: str = "mp3"):
    """Suno's official download: a playable file (format mp3 or m4a). The first download of
    a song uses one of the account's downloads (GET /credits: downloads_left); later ones of
    the same song are free. 402 when Suno refuses it (no downloads left)."""
    import tempfile
    if format not in ("mp3", "m4a"):
        raise HTTPException(400, "format is mp3 or m4a")
    try:
        out = Path(tempfile.gettempdir()) / f"suno_{clip_id}.{format}"
        sc.download_file(clip_id, out, format)
        return FileResponse(out, media_type="audio/mpeg" if format == "mp3" else "audio/mp4",
                            filename=f"suno_{clip_id}.{format}")
    except SunoError as e:
        raise HTTPException(402 if e.status in (402, 403) else 409, str(e))


@app.get("/wait/{batch_id}")
def wait_batch(batch_id: str, timeout_s: int = 300):
    """Find clips belonging to a batch (they carry the batch id prefix info
    only implicitly) - simplest robust way: wait for the given clip ids."""
    raise HTTPException(400, "use POST /generate with wait=true instead")


@app.post("/lyrics")
def lyrics(body: LyricsBody):
    return _guard(sc.generate_lyrics, body.instruction)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("SUNO_HOST", "127.0.0.1"),
                port=int(os.environ.get("SUNO_PORT", "8000")))
