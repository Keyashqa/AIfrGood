# Ydhya — Backend

The Ydhya backend is an AI-powered clinical triage engine for emergency departments. It combines an XGBoost ML classifier with a council of specialised LLM agents (via Google ADK) to produce structured clinical verdicts, and exposes a FastAPI server with JWT auth, PostgreSQL persistence, SSE streaming, and ReportLab PDF generation.

---

## System Architecture

### Agent Pipeline

```
Patient Input → IngestAgent → ClassificationAgent → SpecialistCouncil (Parallel) → CMOAgent → Verdict
```

| Stage | Agent | Type | Role |
|-------|-------|------|------|
| 1 | **IngestAgent** | LlmAgent | Validates and normalises raw patient input |
| 2 | **ClassificationAgent** | BaseAgent (XGBoost) | Predicts Low / Medium / High risk from vitals + comorbidities |
| 3 | **SpecialistCouncil** | Parallel LLM group | 6 specialists evaluate concurrently — Cardiology, Neurology, Pulmonology, Emergency Medicine, General Medicine, Other Specialty |
| 4 | **CMOAgent** | Meta-reasoner LLM | Synthesises council opinions, resolves conflicts, produces final structured verdict with treatment plan and bridging care |

### Post-Processing (`server.py`)

After the ADK pipeline completes, the server enriches the raw CMO output with:
- Consolidated workup (deduped, sorted by STAT → URGENT → ROUTINE)
- Safety alerts (RED_FLAG → CRITICAL, YELLOW_FLAG → WARNING)
- Specialist summaries and council consensus label (Unanimous / Majority / Split)
- Priority score (0–100, MOHFW P1–P4 scale)
- Dissenting opinions and secondary department flags

---

## Directory Structure

```
backend/
├── server.py                   # FastAPI app — CORS, auth, routes, SSE, PDF
├── auth.py                     # JWT creation, verification, bcrypt hashing
├── db.py                       # PostgreSQL helpers (psycopg2)
├── no_llm_server.py            # Lightweight server (ML-only, no LLM)
├── app/
│   ├── agent.py                # Root SequentialAgent
│   ├── config.py               # Gemini model config (gemini-2.5-flash-lite)
│   └── sub_agents/
│       ├── IngestAgent/
│       ├── ClassificationAgent/
│       ├── CMOAgent/
│       └── SpecialistCouncil/
│           └── sub_agents/
│               ├── CardiologyAgent/
│               ├── NeurologyAgent/
│               ├── PulmonologyAgent/
│               ├── EmergencyMedicine/
│               ├── GeneralMedicine/
│               └── OtherSpecialityAgent/
├── services/
│   ├── ml_classifier.py        # XGBoost inference wrapper
│   └── pdf_generator.py        # ReportLab clinical handover PDF
├── model/
│   ├── model.pkl               # Trained XGBoost classifier
│   └── label_encoder.pkl       # Risk-level label encoder
└── .env                        # Secrets (not committed)
```

---

## Setup

### Prerequisites

- Python 3.10+
- PostgreSQL instance
- Google AI Studio API key (`GOOGLE_API_KEY`)

### Installation

```bash
cd backend
pip install fastapi uvicorn google-adk python-dotenv pydantic \
            xgboost scikit-learn reportlab bcrypt python-jose psycopg2
```

### Environment Variables

Create `backend/.env`:

```env
GOOGLE_API_KEY=your_google_ai_studio_key
JWT_SECRET=any_random_secret_string
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

### Run

```bash
uvicorn server:app --reload --port 8000
```

Tables are created automatically via `db.init_db()` on first startup. Safe `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations run on every startup.

---

## API Reference

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register a new doctor account |
| POST | `/api/auth/login` | Login — returns JWT + doctor profile |
| PUT | `/api/auth/facility` | Update doctor's facility level |

### Triage

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/triage` | Start triage session — returns `{ session_id }` |
| GET | `/api/triage/stream/{session_id}` | SSE stream — events: `status`, `classification_result`, `specialist_opinion`, `other_specialty_scores`, `cmo_verdict`, `complete`, `error` |

### Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dashboard/patients` | All active patients for the logged-in doctor |
| GET | `/api/dashboard/stats` | Aggregate stats (risk distribution, dept load, alert counts) |

### Patient Actions *(JWT required)*

| Method | Endpoint | Description |
|--------|----------|-------------|
| DELETE | `/api/patients/{session_id}` | Discharge patient |
| GET | `/api/patients/{session_id}/notes` | Fetch saved doctor's notes |
| POST | `/api/patients/{session_id}/notes` | Save doctor's notes |
| GET | `/api/patients/{session_id}/report.pdf` | Download PDF clinical handover report |

---

## PDF Report

Generated with **ReportLab** (`services/pdf_generator.py`). Sections:

1. Header — system name, timestamp, confidentiality notice
2. Patient details + vitals (temperature in °F)
3. Risk assessment strip (colour-coded: High=red, Medium=orange, Low=green)
4. CMO verdict — explanation, key factors, council consensus, confidence
5. Safety alerts — CRITICAL (red) and WARNING (orange)
6. Workup recommendations table — Test / Priority / Ordered By / Rationale
7. Specialist council summary table — Specialty / Relevance / Urgency / Confidence / Assessment
8. Management plan — Priority / Action / Rationale / Guideline Basis
9. Bridging care — Action / Rationale / Timing (facility-level language)
10. Referral guide — urgency level, time window, criteria table
11. Facility resource checklist — equipment, drugs, personnel (3-column with checkboxes)
12. Doctor's notes — rendered only if notes have been saved
13. AI disclaimer

---

## Database Schema

```sql
CREATE TABLE doctors (
    id             SERIAL PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL,
    password       TEXT NOT NULL,          -- bcrypt hash
    name           TEXT NOT NULL,
    facility_level TEXT DEFAULT 'District Hospital',
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE TABLE patients (
    session_id     TEXT PRIMARY KEY,
    doctor_id      INTEGER NOT NULL REFERENCES doctors(id),
    patient_data   TEXT NOT NULL,          -- JSON (demographics + vitals)
    classification TEXT,                   -- JSON (XGBoost ML result)
    verdict        TEXT,                   -- JSON (enriched CMO verdict)
    doctor_notes   TEXT,                   -- JSON (clinical impression + suggestions)
    status         TEXT DEFAULT 'active',  -- active | discharged
    timestamp      TEXT NOT NULL,
    in_time        TEXT                    -- session start timestamp
);
```

---

## AI Details

- **ML Model**: XGBoost Classifier — predicts Low / Medium / High risk from vitals and comorbidities (30 symptoms + 13 conditions + vital signs)
- **LLM**: `gemini-2.5-flash-lite` for all specialist and CMO agents
- **Safety principle**: CMO applies a worst-case escalation rule — any credible RED_FLAG from any specialist overrides a lower ML risk prediction
- **Facility awareness**: CMO and bridging care instructions adapt to facility level (PHC / District Hospital / Tertiary Medical College)
- **Agent framework**: Google ADK (`SequentialAgent` → `ParallelAgent` → `LlmAgent` / `BaseAgent`)
