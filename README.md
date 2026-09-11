# Meesho UTI Trust Score — Render-ready demo

Working model of the **User Trust Index (UTI)** / Return Shield from the Meesho DICE C2M case.

- Deterministic UTI score (0–900) for customers
- Checkout policy (COD / OTP / prepaid gates)
- Manufacturer + Valmo rider scores
- Dispute attribution rules (damage → manufacturer, tamper/empty → Meesho/delivery)
- **Gemini** explains disputes (optional; rules always decide liability)
- Reverse-feedback API for manufacturers rating buyers

## Quick start (local)

Requires **Python 3.12** (3.14 is too new for current pydantic wheels).

```bash
cd scoring_system
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional but recommended
cp .env.example .env
# put your key in .env — or export it:
export GEMINI_API_KEY="your_key_here"

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open: http://127.0.0.1:8000

> Tip: on macOS/Linux you can load `.env` with:  
> `export $(grep -v '^#' .env | xargs)` before running uvicorn.

## Deploy on Render

1. Push the `scoring_system` folder to a GitHub repo (or the whole Meesho project).
2. Render Dashboard → **New → Web Service** → connect repo.
3. Settings:
   - **Root Directory:** leave empty (this repo *is* the app root)
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. **Environment variables (required):**
   - `PYTHON_VERSION` = `3.12.7` (**must set** — Render defaults to 3.14, which breaks pydantic)
   - `GEMINI_API_KEY` = your Gemini key (**required for AI narratives**)
   - `GEMINI_MODEL` = `gemini-3.6-flash` (optional)
5. Deploy. Health check: `GET /health`

> Repo also includes `.python-version` (`3.12.7`) so Render picks 3.12 even if you forget the env var.

Or use Blueprint: `render.yaml` in this folder.

### Security

- Never commit `.env` or paste the API key into code.
- Gemini key stays in Render env vars only.
- Liability decisions are **rule-based**; Gemini cannot override who pays.

## API cheat sheet

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + Gemini configured? |
| GET | `/api/leaderboard` | Ranked UTI list |
| GET | `/api/score/customer/{id}` | Score one customer |
| POST | `/api/score/customer` | Upsert signals + score |
| POST | `/api/dispute/analyze` | Return Shield + Gemini |
| POST | `/api/feedback/reverse` | Manufacturer rates buyer |

### Example dispute (JSON)

```bash
curl -X POST https://YOUR-SERVICE.onrender.com/api/dispute/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "ORD-99",
    "customer_id": "CUST-420",
    "manufacturer_id": "MFR-SURAT-01",
    "delivery_partner_id": "VALMO-R-77",
    "category": "apparel",
    "return_reason": "Empty box received",
    "empty_box_reported": true,
    "package_seal_intact": false,
    "photos_show_damage": false,
    "delivery_otp_verified": false
  }'
```

## Project layout

```
scoring_system/
  app/
    main.py           # FastAPI routes + UI
    scoring.py        # UTI math + policies
    gemini_client.py  # Gemini dispute narratives
    store.py          # In-memory demo store
  data/seed.json
  templates/
  static/
  requirements.txt
  Procfile
  render.yaml
```

## Note on storage

Demo uses **in-memory** storage (resets on restart). Fine for DICE demo / Render free tier. For production, swap `store.py` to Postgres.
