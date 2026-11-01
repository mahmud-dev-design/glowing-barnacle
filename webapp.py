import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl, quote

from aiohttp import web
from sqlalchemy import create_engine, text

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
OFFERWALL_URL = os.getenv("OFFERWALL_URL", "").strip()
OFFERWALL_PUBLIC_KEY = os.getenv("OFFERWALL_PUBLIC_KEY", "").strip()
OFFERWALL_SECRET = os.getenv("OFFERWALL_SECRET", "").strip()
POINTS_PER_REWARD = float(os.getenv("POINTS_PER_REWARD", "1"))

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


def make_offerwall_url(user_id: int):
    """Create Offerwall.me's required short-lived signed user URL."""
    if not OFFERWALL_PUBLIC_KEY or not OFFERWALL_SECRET:
        return OFFERWALL_URL
    expires = str(int(__import__("time").time()) + 3600)
    message = f"offerwall-user-v1\n{OFFERWALL_PUBLIC_KEY}\n{user_id}\n{expires}"
    signature = hmac.new(
        OFFERWALL_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"https://offerwall.me/offerwall/{quote(OFFERWALL_PUBLIC_KEY, safe='')}"
        f"/{quote(str(user_id), safe='')}?identityExpires={expires}"
        f"&identitySignature={signature}"
    )


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="offerwall-verification" content="6ae7b427ba0c5dd5303e9b0a">
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
            "offerwall_url": make_offerwall_url(user["id"]),
        })
    except Exception:
        return web.json_response({"error": "Service temporarily unavailable."}, status=503)



async def postback(request):
    """Offerwall.me S2S callback; accepts query, form, or JSON POST data."""
    if not OFFERWALL_SECRET:
        return web.Response(text="postback not configured", status=503)

    # Offerwall.me sends HTTP POST. Accept query parameters too for provider tests.
    params = dict(request.rel_url.query)
    try:
        if request.method == "POST":
            ctype = request.content_type.lower()
            if ctype == "application/json":
                body = await request.json()
                if isinstance(body, dict):
                    params.update({str(k): str(v) for k, v in body.items() if v is not None})
            else:
                form = await request.post()
                params.update({str(k): str(v) for k, v in form.items() if v is not None})
    except Exception:
        return web.Response(text="invalid request body", status=400)

    sub_id = params.get("subId", "").strip()
    trans_id = params.get("transId", "").strip()
    reward_raw = params.get("reward", "").strip()
    status = params.get("status", "").strip()
    signature = params.get("signature", "").strip()
    if not all((sub_id, trans_id, reward_raw, status, signature)):
        return web.Response(text="missing parameters", status=400)
    if status not in ("1", "2"):
        return web.Response(text="ignored", status=200)

    try:
        user_id = int(sub_id)
        reward = float(reward_raw)
        if user_id <= 0 or not trans_id or not (reward >= 0):
            raise ValueError()
        points = int(round(reward * POINTS_PER_REWARD))
        if status == "1" and points <= 0:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        return web.Response(text="invalid values", status=400)

    # Exact Offerwall.me formula: md5(subId + transId + reward + secretKey).
    expected = hashlib.md5(
        f"{sub_id}{trans_id}{reward_raw}{OFFERWALL_SECRET}".encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(expected.lower(), signature.lower()):
        return web.Response(text="invalid signature", status=403)

    try:
        with engine.begin() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS offerwall_transactions (
                trans_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, points INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
            old = conn.execute(text(
                "SELECT status, points, user_id FROM offerwall_transactions WHERE trans_id=:tid FOR UPDATE"
            ), {"tid": trans_id}).fetchone()

            if status == "1":
                if old:
                    return web.Response(text="duplicate", status=200)
                user = conn.execute(text(
                    "SELECT points FROM users WHERE user_id=:uid FOR UPDATE"
                ), {"uid": user_id}).fetchone()
                if not user:
                    return web.Response(text="user not found", status=404)
                conn.execute(text("UPDATE users SET points=points+:p WHERE user_id=:u"),
                             {"p": points, "u": user_id})
                conn.execute(text("""INSERT INTO offerwall_transactions
                    (trans_id,user_id,points,status) VALUES (:t,:u,:p,'approved')"""),
                             {"t": trans_id, "u": user_id, "p": points})
            else:
                if not old or old[0] != "approved":
                    return web.Response(text="already reversed or unknown", status=200)
                # Use the original credited account and amount for a safe reversal.
                original_user_id = int(old[2])
                conn.execute(text("UPDATE users SET points=GREATEST(points-:p,0) WHERE user_id=:u"),
                             {"p": int(old[1]), "u": original_user_id})
                conn.execute(text("UPDATE offerwall_transactions SET status='reversed' WHERE trans_id=:t"),
                             {"t": trans_id})
        return web.Response(text="ok", status=200)
    except Exception:
        return web.Response(text="temporary error", status=500)


async def health(request):
    return web.Response(text="ok")


app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/api/me", me)
app.router.add_get("/api/postback", postback)
app.router.add_post("/api/postback", postback)
app.router.add_get("/health", health)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
