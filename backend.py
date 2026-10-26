"""OneMix SoundCloud test: one artist-enabled downloadable track, on a separate Render service.

Uses yt-dlp's strict 'download' format: never saves SoundCloud's stream-only formats.
Requires explicit permission for the configured recording. This is NOT the OneMix
production downloader and does not accept arbitrary download URLs from visitors.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL

APP_SECRET = os.getenv("AUDIO_IMPORT_SECRET", "")
TEST_URL = os.getenv("SOUNDCLOUD_TEST_URL", "").strip()
RIGHTS_CONFIRMED = os.getenv("SOUNDCLOUD_RIGHTS_CONFIRMED", "").strip().lower() == "yes"
MAX_BYTES = 35 * 1024 * 1024
COOKIE_NAME = "onemix_sc_test"
COOKIE_SECONDS = 60 * 60 * 2
ALLOWED_EXTENSIONS = {"mp3", "m4a", "wav", "aac", "flac", "aiff", "aif"}

app = FastAPI(title="OneMix SoundCloud Authorized Test", docs_url=None, redoc_url=None)
STATE = {"status": "idle", "error": None, "errorType": None, "title": None, "uploader": None,
         "format": None, "sizeBytes": None, "elapsedSeconds": None}
ACTIVE_TASK = None
AUDIO_PATH: Path | None = None
AUDIO_FOLDER: Path | None = None


def valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and
                (parsed.hostname or "").lower() in {"soundcloud.com", "www.soundcloud.com"} and
                len([p for p in parsed.path.split("/") if p]) == 2 and
                not parsed.username and not parsed.password and
                not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def configured() -> bool:
    return len(APP_SECRET) >= 20 and RIGHTS_CONFIRMED and valid_url(TEST_URL)


def issue_cookie() -> str:
    expiry = int(time.time()) + COOKIE_SECONDS
    signature = hmac.new(APP_SECRET.encode(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def authorized(cookie: str | None) -> bool:
    if not APP_SECRET or not cookie:
        return False
    try:
        expiry_txt, sig = cookie.split(".", 1)
        expiry = int(expiry_txt)
        if expiry <= time.time() or expiry > time.time() + COOKIE_SECONDS + 10:
            return False
        expected = hmac.new(APP_SECRET.encode(), expiry_txt.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except (ValueError, TypeError):
        return False


def require_auth(cookie: str | None):
    if not authorized(cookie):
        raise HTTPException(401, "Please sign in on the /test page first.")


class Login(BaseModel):
    secret: str


@app.get("/health")
def health():
    return {"ok": True, "source": "soundcloud-yt-dlp-original-download-only",
            "configured": configured(), "status": STATE["status"],
            "testTrackConfigured": bool(TEST_URL), "rightsConfirmed": RIGHTS_CONFIRMED}


@app.get("/")
def home():
    return RedirectResponse("/test", status_code=302)


@app.get("/test", response_class=HTMLResponse)
def test_page():
    return HTMLResponse("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OneMix • SoundCloud Test</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0c1016;color:#eef2f7;font:16px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}
main{max-width:620px;margin:7vh auto;padding:24px}h1{font-size:27px;margin-bottom:8px}p{color:#a9b6c6;line-height:1.55}
section{border:1px solid #303c47;border-radius:18px;background:#141d26;padding:22px;margin:20px 0}
input,button{font:inherit;padding:13px;border-radius:11px}input{width:100%;background:#101922;border:1px solid #455365;color:#fff}
button{background:#b4e8f5;border:0;color:#081218;font-weight:700;margin-top:12px;cursor:pointer}button:disabled{opacity:.5}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;color:#b7daeb;line-height:1.5}
a{color:#b4e8f5}audio{width:100%;margin-top:12px}
</style></head><body><main><h1>OneMix • SoundCloud test</h1>
<p>Separate from your working OneMix app. Tests one configured track whose creator allows the original file to be downloaded.</p>
<section><label for="secret">Your Render AUDIO_IMPORT_SECRET</label><input id="secret" type="password" autocomplete="off" placeholder="Paste your existing secret">
<button id="login">Unlock test</button><p id="loginMessage"></p></section>
<section><h2>Audio test</h2><p>No Spotify, YouTube or stream-only fallback. Render uses yt-dlp's <code>download</code> format only.</p>
<button id="run" disabled>Start authorized download test</button>
<pre id="out">Sign in to begin.</pre><audio id="audio" controls preload="none" style="display:none"></audio></section>
<p>Only use a SoundCloud track whose original file the creator offers for download and for which you have permission to store and play it here. A 429 still means SoundCloud is rate-limiting Render.</p>
</main><script>
const $ = (x)=>document.getElementById(x);
const out = $('out');let pollTimer=null;
async function call(path,opts={}){const r=await fetch(path,{credentials:'same-origin',...opts});const j=await r.json();if(!r.ok)throw Error(j.detail||JSON.stringify(j));return j}
async function poll(){try{const j=await call('/status');out.textContent=JSON.stringify(j,null,2);
if(j.status==='ready'){$('audio').style.display='block';$('audio').src='/audio';$('audio').load();clearInterval(pollTimer);pollTimer=null;}
if(j.status==='failed'){clearInterval(pollTimer);pollTimer=null;$('run').disabled=false;}
}catch(e){out.textContent=String(e);clearInterval(pollTimer);pollTimer=null;}}
$('login').onclick=async()=>{try{await call('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({secret:$('secret').value})});$('secret').value='';$('loginMessage').textContent='Unlocked. You can run the test.';$('run').disabled=false;await poll();}
catch(e){$('loginMessage').textContent=String(e)}};
$('run').onclick=async()=>{$('run').disabled=true;$('audio').pause();$('audio').style.display='none';
try{await call('/start',{method:'POST'});await poll();if(!pollTimer)pollTimer=setInterval(poll,2000)}catch(e){out.textContent=String(e);$('run').disabled=false}};
</script></body></html>""")


@app.post("/login")
def login(payload: Login):
    if not configured():
        raise HTTPException(503, "Configure AUDIO_IMPORT_SECRET, SOUNDCLOUD_TEST_URL and SOUNDCLOUD_RIGHTS_CONFIRMED=yes on Render.")
    if not secrets.compare_digest(payload.secret, APP_SECRET):
        raise HTTPException(401, "Incorrect secret")
    result = JSONResponse({"ok": True})
    result.set_cookie(COOKIE_NAME, issue_cookie(), max_age=COOKIE_SECONDS,
                      httponly=True, secure=True, samesite="strict", path="/")
    result.headers["Cache-Control"] = "no-store"
    return result


@app.get("/status")
def get_status(onemix_sc_test: str | None = Cookie(default=None)):
    require_auth(onemix_sc_test)
    return {**STATE, "readyToPlay": bool(STATE["status"] == "ready" and AUDIO_PATH and AUDIO_PATH.exists())}


def perform_test():
    """Download only SoundCloud's creator-offered original 'download' format, never a stream."""
    folder = Path(tempfile.mkdtemp(prefix="onemix-sc-"))
    try:
        opts = {
            "format": "download",  # strict: will fail instead of selecting http/hls streams
            "outtmpl": str(folder / "sample.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "socket_timeout": 20,
            "max_filesize": MAX_BYTES,
            "windowsfilenames": True,
        }
        started = time.monotonic()
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(TEST_URL, download=True)
        if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
            raise RuntimeError("Expected one SoundCloud track, not a playlist")
        if info.get("format_id") != "download":
            raise RuntimeError("The creator-enabled original download format was not selected")
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower().lstrip(".") in ALLOWED_EXTENSIONS]
        if len(files) != 1:
            raise RuntimeError("No supported downloadable audio file found; use a downloadable MP3 or M4A track for this test")
        size = files[0].stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise RuntimeError("The audio file is empty or exceeds 35 MB")
        return folder, files[0], {
            "title": str(info.get("title") or "Unknown")[:150],
            "uploader": str(info.get("uploader") or "Unknown")[:150],
            "format": files[0].suffix.lower().lstrip("."),
            "sizeBytes": size,
            "elapsedSeconds": round(time.monotonic() - started, 1),
        }
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


async def run_test():
    global AUDIO_PATH, AUDIO_FOLDER
    try:
        folder, audio, details = await asyncio.to_thread(perform_test)
        old_folder = AUDIO_FOLDER
        AUDIO_FOLDER, AUDIO_PATH = folder, audio
        STATE.update({"status": "ready", "error": None, "errorType": None, **details})
        print(f"SoundCloud test succeeded: {details['sizeBytes']} bytes, {details['format']}", flush=True)
        if old_folder and old_folder != folder:
            shutil.rmtree(old_folder, ignore_errors=True)
    except Exception as exc:
        kind = type(exc).__name__
        message = str(exc)[:450]
        STATE.update({"status": "failed", "errorType": kind, "error": message})
        print(f"SoundCloud test failed: {kind}: {message}", flush=True)


@app.post("/start", status_code=202)
async def start(onemix_sc_test: str | None = Cookie(default=None)):
    global ACTIVE_TASK
    require_auth(onemix_sc_test)
    if not configured():
        raise HTTPException(503, "Test service is not configured")
    if STATE["status"] == "processing":
        return {"status": "processing"}
    if STATE["status"] == "ready" and AUDIO_PATH and AUDIO_PATH.exists():
        return {"status": "ready", "cached": True}
    STATE.update({"status": "processing", "errorType": None, "error": None,
                  "title": None, "uploader": None, "format": None, "sizeBytes": None, "elapsedSeconds": None})
    print("SoundCloud original-download test started", flush=True)
    ACTIVE_TASK = asyncio.create_task(run_test())
    return {"status": "processing"}


@app.get("/audio")
def audio(onemix_sc_test: str | None = Cookie(default=None)):
    require_auth(onemix_sc_test)
    if STATE["status"] != "ready" or not AUDIO_PATH or not AUDIO_PATH.exists():
        raise HTTPException(404, "No audio test file is ready")
    response = FileResponse(AUDIO_PATH, media_type=mimetypes.guess_type(AUDIO_PATH.name)[0] or "application/octet-stream",
                            filename=None, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    return response
