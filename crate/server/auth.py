"""Optional password gate.

Off by default: run it on your own machine and there is nothing to log into.
Set CRATE_PASSWORD_HASH and the whole app requires a session — which is what
you want the moment it answers on a public address, because every endpoint here
can spend disk and bandwidth on your behalf.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..config import Settings
from ..security import sign_session, verify_password, verify_session

COOKIE = "crate_session"
OPEN_PATHS = ("/login", "/api/health", "/static/")

router = APIRouter()

LOGIN_PAGE = """<!doctype html><meta charset=utf-8>
<title>Crate Digger</title><link rel=stylesheet href=/static/style.css>
<style>
  body {{ display: grid; place-items: center; height: 100vh; }}
  form {{ background: var(--panel); border: 1px solid var(--line);
          border-radius: 10px; padding: 26px; width: min(340px, 90vw);
          display: flex; flex-direction: column; gap: 12px; }}
  .err {{ color: var(--red); font-size: 12px; }}
</style>
<form method=post action=/login>
  <h2>Crate Digger</h2>
  {error}
  <input type=password name=password placeholder="Password" autofocus required>
  <button class="btn btn-primary" type=submit>Open the crate</button>
</form>"""


def is_enabled(settings: Settings) -> bool:
    return bool(settings.password_hash)


def is_authorised(request: Request, settings: Settings) -> bool:
    if not is_enabled(settings):
        return True
    header = request.headers.get("authorization", "")
    if settings.api_key and header == f"Bearer {settings.api_key}":
        return True
    token = request.cookies.get(COOKIE, "")
    return bool(token and verify_session(settings.session_secret, token))


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_form() -> str:
    return LOGIN_PAGE.format(error="")


@router.post("/login", include_in_schema=False)
async def login(request: Request, password: str = Form(...)):
    settings = request.app.state.crate.settings
    if not is_enabled(settings):
        return RedirectResponse("/", status_code=303)
    if not verify_password(password, settings.password_hash or ""):
        return HTMLResponse(
            LOGIN_PAGE.format(error='<p class="err">That is not the password.</p>'),
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE, sign_session(settings.session_secret),
        httponly=True, samesite="lax", max_age=30 * 24 * 3600,
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


async def guard(request: Request, call_next):
    settings = request.app.state.crate.settings
    path = request.url.path
    if is_enabled(settings) and not path.startswith(OPEN_PATHS):
        if not is_authorised(request, settings):
            # Middleware sits outside the exception handlers, so an
            # HTTPException raised here escapes as a 500 — return the response.
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not logged in"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)
