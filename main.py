"""
WorkLens API Server v2

Public endpoints (require wl_ client token):
  GET  /health
  POST /validate_token
  POST /analyze
  POST /transcribe

Admin endpoints (require X-Admin-Key header):
  GET  /admin               -- admin UI (open in browser with ?key=YOUR_ADMIN_KEY)
  POST /admin/tokens        -- create token for new client
  GET  /admin/tokens        -- list all tokens as JSON
  POST /admin/tokens/deactivate
"""
import os
import json
import logging
import secrets
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worklens-api")

app = FastAPI(title="WorkLens API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ─── Config ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ADMIN_KEY      = os.environ.get("ADMIN_KEY", "")
DB_PATH        = "/tmp/worklens_tokens.db"

SYSTEM_PROMPT = (
    "You are a work assistant. Analyze the message and return ONLY valid JSON. "
    "No explanations. No markdown. Just the JSON object.\n\n"
    "JSON schema (all fields required):\n"
    '{"has_task":bool,"has_deadline":bool,"deadline_text":string|null,'
    '"has_agreement":bool,"has_question":bool,"action_required":bool,'
    '"urgency":"low"|"medium"|"high","has_file_request":bool}'
)

# ─── DB ───────────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                token      TEXT UNIQUE NOT NULL,
                company    TEXT NOT NULL,
                email      TEXT NOT NULL,
                active     INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_used  TEXT
            )
        """)
        conn.commit()

init_db()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def generate_token() -> str:
    return "wl_" + secrets.token_urlsafe(24)

def check_token(token: str):
    if not token or not token.startswith("wl_"):
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tokens WHERE token=? AND active=1", (token,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE tokens SET last_used=datetime('now') WHERE token=?", (token,)
            )
        return dict(row) if row else None

def require_admin(key: str):
    if not ADMIN_KEY or key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")

def empty_intent() -> dict:
    return {
        "has_task": False, "has_deadline": False, "deadline_text": None,
        "has_agreement": False, "has_question": False,
        "action_required": False, "urgency": "low", "has_file_request": False
    }

def validate_intent(raw: dict) -> dict:
    result = empty_intent()
    for k in result:
        if k in raw:
            result[k] = raw[k]
    if result["urgency"] not in ("low", "medium", "high"):
        result["urgency"] = "medium"
    return result

# ─── Models ───────────────────────────────────────────────────────────────────
class AnalyzeReq(BaseModel):
    text: str
    token: str
    source: str = "telegram"


class AnalyzePatternsReq(BaseModel):
    data: str
    token: str

class TranscribeReq(BaseModel):
    audio_b64: str
    token: str

class ValidateReq(BaseModel):
    token: str

class CreateTokenReq(BaseModel):
    company: str
    email: str

# ─── Public routes ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "worklens-api", "version": "2.0.0"}

@app.post("/validate_token")
async def validate_token_ep(req: ValidateReq):
    row = check_token(req.token)
    if not row:
        return {"ok": False, "error": "Invalid or inactive token"}
    return {"ok": True, "company": row["company"], "email": row["email"]}

@app.post("/analyze")
async def analyze(req: AnalyzeReq):
    if not check_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid token")
    if not req.text or not req.text.strip():
        return {"ok": True, "intent": empty_intent()}
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Service not configured")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": req.text.strip()[:2000]}
                    ],
                    "temperature": 0.1, "max_tokens": 200,
                    "response_format": {"type": "json_object"}
                }
            )
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Rate limit")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="OpenAI error")
        raw = json.loads(resp.json()["choices"][0]["message"]["content"])
        return {"ok": True, "intent": validate_intent(raw), "source": req.source}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analyze: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/transcribe")
async def transcribe(req: TranscribeReq):
    if not check_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid token")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Service not configured")
    try:
        import base64, tempfile
        audio_bytes = base64.b64decode(req.audio_b64)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_bytes); tmp = f.name
        async with httpx.AsyncClient(timeout=60) as client:
            with open(tmp, "rb") as af:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("audio.ogg", af, "audio/ogg")},
                    data={"model": "whisper-1"}
                )
        Path(tmp).unlink(missing_ok=True)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Whisper error")
        return {"ok": True, "transcript": resp.json().get("text", "")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcribe: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Install / landing page ───────────────────────────────────────────────────
@app.get("/install", response_class=HTMLResponse)
async def install_page():
    html = Path(__file__).parent / "install.html"
    return html.read_text(encoding="utf-8")


@app.get("/install/mac")
async def install_mac(token: str = ""):
    row = check_token(token) if token else None
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    script = Path(__file__).parent / "install_mac.sh"
    content = script.read_text(encoding="utf-8")
    # Embed token into installer
    content = content.replace(
        "WORKLENS_TOKEN=""",
        f'WORKLENS_TOKEN="{token}"'
    )
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="application/x-sh",
        headers={"Content-Disposition": "attachment; filename=install_worklens.sh"}
    )


@app.get("/install/win")
async def install_win(token: str = ""):
    row = check_token(token) if token else None
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    script = Path(__file__).parent / "install_win.bat"
    content = script.read_text(encoding="utf-8")
    content = content.replace(
        "WORKLENS_TOKEN=",
        f'WORKLENS_TOKEN={token}'
    )
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=install_worklens.bat"}
    )


@app.post("/analyze_patterns")
async def analyze_patterns(req: AnalyzePatternsReq):
    """
    Receives structured activity summary text from WorkLens client.
    Calls GPT-4o to generate automation suggestions.
    Returns JSON array of suggestions.
    """
    if not check_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid token")
    if not req.data or len(req.data.strip()) < 50:
        raise HTTPException(status_code=400, detail="Insufficient data")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Service not configured")

    system_prompt = """You are a business process automation expert.

Analyze this employee work activity data and identify TOP automation opportunities.

Return a JSON object with key "suggestions" containing an array (max 6 items), sorted by impact:
{
  "suggestions": [
    {
      "title": "Short title (max 8 words)",
      "description": "What the employee does manually and why automate (2-3 sentences)",
      "evidence": "Specific data proving this pattern",
      "time_per_week_hours": 3.5,
      "automation_tool": "Specific tool: Zapier / Python script / Make.com / API integration / etc",
      "implementation_hours": 8,
      "priority": "high|medium|low",
      "category": "data_transfer|reporting|communication|file_processing|approval"
    }
  ]
}

Rules:
- Base suggestions ONLY on provided data
- time_per_week_hours must be realistic based on event counts (5 sec each)
- If an app has 1000 events = ~1.4h total tracked
- implementation_hours: 2-40 range
- Return ONLY valid JSON, no markdown
- IMPORTANT: Write ALL text fields (title, description, evidence, automation_tool) in RUSSIAN language"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": req.data[:4000]}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                    "response_format": {"type": "json_object"}
                }
            )
        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Rate limit")
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"OpenAI error {resp.status_code}")

        raw = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        suggestions = parsed.get("suggestions", [])
        return {"ok": True, "suggestions": suggestions}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"analyze_patterns error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── Admin routes ─────────────────────────────────────────────────────────────
ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>WorkLens Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#f5f6fa;min-height:100vh;padding:32px 24px}
.wrap{max-width:1000px;margin:0 auto}
h1{color:#4caf50;font-size:22px;margin-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}
.sc{background:#fff;border-radius:12px;padding:16px;text-align:center;
    box-shadow:0 1px 4px rgba(0,0,0,.06)}
.sv{font-size:28px;font-weight:700;color:#4caf50}
.sl{font-size:12px;color:#888;margin-top:3px}
.card{background:#fff;border-radius:14px;padding:20px 24px;
      box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;color:#888;
         text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center}
input{flex:1;border:1.5px solid #e0e0e0;border-radius:8px;
      padding:10px 13px;font-size:14px;outline:none}
input:focus{border-color:#4caf50}
.btn{background:#4caf50;color:#fff;border:none;padding:10px 20px;
     border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}
.btn:hover{background:#43a047}
.btn-red{background:#ef5350}.btn-red:hover{background:#e53935}
.btn-sm{padding:5px 12px;font-size:12px}
.result{margin-top:12px;padding:12px;background:#e8f5e9;border-radius:8px;
        font-size:13px;display:none}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #f0f0f0;
      font-size:13px}
th{font-weight:600;color:#888;font-size:11px;text-transform:uppercase}
.tok{font-family:monospace;font-size:11px;color:#555;
     background:#f5f5f5;padding:2px 6px;border-radius:4px}
.act{color:#2e7d32;font-weight:600}.ina{color:#e53935}
</style></head><body><div class="wrap">
<h1>WorkLens Admin</h1>
<div class="stats">
  <div class="sc"><div class="sv" id="s-total">—</div><div class="sl">Total clients</div></div>
  <div class="sc"><div class="sv" id="s-active">—</div><div class="sl">Active</div></div>
  <div class="sc"><div class="sv" id="s-used">—</div><div class="sl">Used at least once</div></div>
</div>
<div class="card">
  <h3>New client token</h3>
  <div class="row">
    <input id="company" placeholder="Company name (e.g. StroiGroup KZ)">
    <input id="email"   placeholder="Contact email">
    <button class="btn" onclick="create()">Generate token</button>
  </div>
  <div class="result" id="result"></div>
</div>
<div class="card">
  <h3>All clients</h3>
  <table>
    <thead><tr>
      <th>Company</th><th>Email</th><th>Token</th>
      <th>Status</th><th>Created</th><th>Last used</th><th></th>
    </tr></thead>
    <tbody id="tbody"><tr><td colspan="7" style="text-align:center;padding:20px;color:#aaa">Loading...</td></tr></tbody>
  </table>
</div>
</div><script>
const KEY = new URLSearchParams(location.search).get('key')||'';

async function load(){
  const d = await fetch('/admin/tokens',{headers:{'X-Admin-Key':KEY}}).then(r=>r.json());
  if(!d.ok){document.body.innerHTML='<p style="padding:40px;color:red">Invalid admin key</p>';return;}
  const T=d.tokens;
  document.getElementById('s-total').textContent=T.length;
  document.getElementById('s-active').textContent=T.filter(t=>t.active).length;
  document.getElementById('s-used').textContent=T.filter(t=>t.last_used).length;
  document.getElementById('tbody').innerHTML=T.length?T.map(t=>`
    <tr>
      <td><b>${esc(t.company)}</b></td>
      <td>${esc(t.email)}</td>
      <td><span class="tok">${t.token}</span></td>
      <td class="${t.active?'act':'ina'}">${t.active?'Active':'Inactive'}</td>
      <td>${t.created_at.slice(0,10)}</td>
      <td>${t.last_used?t.last_used.slice(0,16):'—'}</td>
      <td>${t.active?`<button class="btn btn-red btn-sm" onclick="deact('${t.token}')">Deactivate</button>`:''}</td>
    </tr>`).join(''):'<tr><td colspan="7" style="text-align:center;padding:20px;color:#aaa">No clients yet</td></tr>';
}

async function create(){
  const company=document.getElementById('company').value.trim();
  const email=document.getElementById('email').value.trim();
  if(!company||!email){alert('Fill in company and email');return;}
  const r=await fetch('/admin/tokens',{method:'POST',
    headers:{'Content-Type':'application/json','X-Admin-Key':KEY},
    body:JSON.stringify({company,email})}).then(r=>r.json());
  const el=document.getElementById('result');
  el.style.display='block';
  if(r.ok){
    el.innerHTML=`Token created for <b>${esc(company)}</b>:<br>
      <span class="tok" style="font-size:13px">${r.token}</span><br>
      <small style="color:#666">Send this token to the client along with the setup instructions.</small>`;
    document.getElementById('company').value='';
    document.getElementById('email').value='';
    load();
  } else { el.style.background='#ffebee'; el.textContent='Error: '+(r.error||'unknown'); }
}

async function deact(token){
  if(!confirm('Deactivate this token? The client will lose access.'))return;
  await fetch('/admin/tokens/deactivate',{method:'POST',
    headers:{'Content-Type':'application/json','X-Admin-Key':KEY},
    body:JSON.stringify({token})});
  load();
}

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
load();
</script></body></html>"""

@app.get("/admin", response_class=HTMLResponse)
async def admin_ui(key: str = "", x_admin_key: str = Header(default="")):
    require_admin(key or x_admin_key)
    return ADMIN_HTML

@app.post("/admin/tokens")
async def create_token(req: CreateTokenReq, x_admin_key: str = Header(default="")):
    require_admin(x_admin_key)
    token = generate_token()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO tokens (token, company, email) VALUES (?,?,?)",
            (token, req.company, req.email)
        )
    logger.info(f"Token created: {req.company} ({req.email})")
    return {"ok": True, "token": token, "company": req.company}

@app.get("/admin/tokens")
async def list_tokens(x_admin_key: str = Header(default="")):
    require_admin(x_admin_key)
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tokens ORDER BY created_at DESC").fetchall()
    return {"ok": True, "tokens": [dict(r) for r in rows]}

@app.post("/admin/tokens/deactivate")
async def deactivate_token(request: Request, x_admin_key: str = Header(default="")):
    require_admin(x_admin_key)
    body = await request.json()
    with get_db() as conn:
        conn.execute("UPDATE tokens SET active=0 WHERE token=?", (body.get("token",""),))
    return {"ok": True}
