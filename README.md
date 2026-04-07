# WorkLens API

Public backend for WorkLens clients. Analyzes messages and transcribes voice using OpenAI, server-side. Clients never see the OpenAI key.

## Endpoints

- `GET /health` — health check
- `POST /analyze` — analyze message intent
- `POST /transcribe` — transcribe voice (base64)

## Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `WORKLENS_TOKENS` | Comma-separated list of valid `wl_` client tokens |

## Deploy on Railway

1. Connect this repo to Railway
2. Set env vars: `OPENAI_API_KEY` and `WORKLENS_TOKENS`
3. Deploy
