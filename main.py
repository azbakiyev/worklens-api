"""
WorkLens API Server
Public backend — receives text from WorkLens clients,
calls OpenAI server-side, returns structured intent JSON.

Endpoints:
  POST /analyze    — analyze message text
  POST /transcribe — transcribe voice (base64 audio)
  GET  /health     — health check
"""
import os
import json
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worklens-api")

app = FastAPI(title="WorkLens API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config from environment variables ────────────────────────────────────────
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
VALID_TOKENS    = set(os.environ.get("WORKLENS_TOKENS", "").split(","))


# ── Models ────────────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    text: str
    token: str
    source: str = "telegram"


class TranscribeRequest(BaseModel):
    audio_b64: str
    token: str


# ── Helpers ───────────────────────────────────────────────────────────────────
def validate_token(token: str) -> bool:
    if not token or not token.startswith("wl_"):
        return False
    # Strip prefix and check against valid tokens
    raw = token.replace("wl_", "")
    return token in VALID_TOKENS or raw in VALID_TOKENS


def empty_intent() -> dict:
    return {
        "has_task": False,
        "has_deadline": False,
        "deadline_text": None,
        "has_agreement": False,
        "has_question": False,
        "action_required": False,
        "urgency": "low",
        "has_file_request": False,
    }


def validate_intent(raw: dict) -> dict:
    result = empty_intent()
    for k in result:
        if k in raw:
            result[k] = raw[k]
    if result["urgency"] not in ("low", "medium", "high"):
        result["urgency"] = "medium"
    return result


SYSTEM_PROMPT = (
    "You are a work assistant. Analyze the message and return ONLY valid JSON. "
    "No explanations. No markdown. Just the JSON object.\n\n"
    'JSON schema (all fields required):\n'
    '{"has_task":bool,"has_deadline":bool,"deadline_text":string|null,'
    '"has_agreement":bool,"has_question":bool,"action_required":bool,'
    '"urgency":"low"|"medium"|"high","has_file_request":bool}'
)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "worklens-api"}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    if not validate_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid WorkLens token")

    if not req.text or not req.text.strip():
        return {"ok": True, "intent": empty_intent()}

    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Service not configured")

    text = req.text.strip()[:2000]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": text},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200,
                    "response_format": {"type": "json_object"},
                },
            )

        if resp.status_code == 429:
            raise HTTPException(status_code=429, detail="Rate limit, retry later")
        if resp.status_code != 200:
            logger.error(f"OpenAI error: {resp.status_code} {resp.text[:200]}")
            raise HTTPException(status_code=502, detail="OpenAI error")

        raw = json.loads(resp.json()["choices"][0]["message"]["content"])
        return {"ok": True, "intent": validate_intent(raw), "source": req.source}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analyze error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    if not validate_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid WorkLens token")

    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Service not configured")

    try:
        import base64, tempfile, pathlib
        audio_bytes = base64.b64decode(req.audio_b64)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        async with httpx.AsyncClient(timeout=60) as client:
            with open(tmp_path, "rb") as audio_file:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("audio.ogg", audio_file, "audio/ogg")},
                    data={"model": "whisper-1"},
                )

        pathlib.Path(tmp_path).unlink(missing_ok=True)

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Whisper error")

        return {"ok": True, "transcript": resp.json().get("text", "")}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcribe error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
