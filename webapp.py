import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl

from aiohttp import web
from sqlalchemy import create_engine, text

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
OFFERWALL_URL = os.getenv("OFFERWALL_URL", "").strip()

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN in this Render service's Environment.")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL in this Render service's Environment.")

DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)


def validate_init_data(init_data: str):
    """Validate Telegram Mini App initData and return its user object."""
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    try:
        user = json.loads(values.get("user", "{}"))
        user_id = int(user["id"])
        return {"id": user_id, "first_name": user.get("first_name", "User")}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def get_points(user_id: int):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT points FROM users WHERE user_id=:uid"),
            {"uid": user_id},
        ).fetchone()
    return int(row[0]) if row else 0


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HB FILE STORE</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;padding:24px;font-family:Arial,sans-serif;background:#101522;color:#fff}
main{max-width:440px;margin:30px auto}
.brand{font-size:13px;letter-spacing:2px;color:#a8b6d8}
h1{font-size:29px;margin:8px 0 22px}
.card{background:#1b2639;border:1px solid #33415d;border-radius:18px;padding:22px;margin:14px 0}
.muted{color:#aab6ce;font-size:14px}
.points{font-size:36px;font-weight:700;margin:8px 0}
button{width:100%;padding:15px;border:0;border-radius:12px;background:#4d8dff;color:white;font-size:16px;font-weight:700}
button:disabled{opacity:.5}
#status{margin-top:16px;line-height:1.5}
</style>
</head>
<body><main>
<div class="brand">HB FILE STORE</div><h1>Earn Points</h1>
<section class="card">
<div class="muted" id="hello">Welcome</div>
<div class="muted">Your points balance</div>
<div class="points" id="points">—</div>
<button id="offer" disabled>Open Offerwall</button>
<div id="status" class="muted">Connecting securely to Telegram…</div>
</section>
<p class="muted">Points are added only after the offer provider confirms completion through a verified server callback.</p>
</main>
<script>
const tg = window.Telegram?.WebApp;
const statusEl = document.getElementById('status');
if (tg) tg.ready();
async function start(){
  if (!tg || !tg.initData) {
    statusEl.textContent = 'Please open this Mini App from the Telegram bot.';
    return;
  }
  try {
    const res = await fetch('/api/me', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({initData:tg.initData})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Authentication failed');
    document.getElementById('hello').textContent = 'Hello, ' + data.first_name;
    document.getElementById('points').textContent = data.points;
    if (data.offerwall_url) {
      const b = document.getElementById('offer');
      b.disabled = false;
      b.onclick = () => tg.openLink(data.offerwall_url);
    } else {
      statusEl.textContent = 'Offerwall is not configured yet. Add the approved provider link in Render Environment.';
    }
  } catch(e) { statusEl.textContent = e.message; }
}
start();
</script></body></html>"""


async def index(request):
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def me(request):
    try:
        payload = await request.json()
        user = validate_init_data(str(payload.get("initData", "")))
        if not user:
            return web.json_response({"error": "Telegram verification failed."}, status=401)
        return web.json_response({
            "first_name": user["first_name"],
            "points": get_points(user["id"]),
            "offerwall_url": OFFERWALL_URL,
        })
    except Exception:
        return web.json_response({"error": "Service temporarily unavailable."}, status=503)


async def health(request):
    return web.Response(text="ok")


app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/api/me", me)
app.router.add_get("/health", health)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
