import os
import re
import json
import uuid
import asyncio
import httpx
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, List, Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
import db
from auth import hash_password, verify_password, create_token, get_current_doctor


# ─────────────────────────────────────────
# Environment
# ─────────────────────────────────────────

load_dotenv()

if not os.getenv("GOOGLE_API_KEY"):
    raise RuntimeError("GOOGLE_API_KEY is not set")
if not os.getenv("JWT_SECRET"):
    raise RuntimeError("JWT_SECRET is not set")


# ─────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────

app = FastAPI(title="TriageAI Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


@app.on_event("startup")
async def startup():
    db.init_db()


# ─────────────────────────────────────────
# ADK Setup
# ─────────────────────────────────────────

APP_NAME = "triage_app"

session_service = InMemorySessionService()

runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)


# ─────────────────────────────────────────
# In-Memory Session Tracking (ADK user_id mapping)
# ─────────────────────────────────────────

active_sessions: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────

class PatientData(BaseModel):
    patient_id: Optional[str] = None
    name: Optional[str] = None
    age: int
    gender: str
    symptoms: List[str]
    bp_systolic: int
    bp_diastolic: int
    heart_rate: int
    temperature: float  # Celsius from frontend
    spo2: int
    conditions: List[str]
    facility_level: Optional[str] = "District Hospital"  # Level 1 PHC / District Hospital / Tertiary Medical College
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    additional_info: Optional[str] = None


class RegisterRequest(BaseModel):
    username: str
    password: str
    name: str
    facility_level: Optional[str] = "District Hospital"


class FacilityUpdateRequest(BaseModel):
    facility_level: str


class LoginRequest(BaseModel):
    username: str
    password: str


class DoctorNotesRequest(BaseModel):
    doctor_name: str
    clinical_impression: str
    suggestions: str


# ─────────────────────────────────────────
# Auth Endpoints
# ─────────────────────────────────────────

@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    existing = db.get_doctor_by_username(req.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
    hashed = hash_password(req.password)
    doctor_id = db.create_doctor(req.username, hashed, req.name, req.facility_level or "District Hospital")
    return {"message": "Doctor registered", "doctor_id": doctor_id}


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    doctor = db.get_doctor_by_username(req.username)
    if not doctor or not verify_password(req.password, doctor["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(doctor["id"])
    return {
        "access_token": token,
        "doctor": {
            "id": doctor["id"],
            "name": doctor["name"],
            "username": doctor["username"],
            "facility_level": doctor.get("facility_level", "District Hospital"),
        },
    }


@app.put("/api/auth/facility")
async def update_facility(req: FacilityUpdateRequest, doctor_id: int = Depends(get_current_doctor)):
    valid_levels = ["Level 1 PHC", "District Hospital", "Tertiary Medical College"]
    if req.facility_level not in valid_levels:
        raise HTTPException(status_code=400, detail=f"facility_level must be one of: {valid_levels}")
    db.update_facility_level(doctor_id, req.facility_level)
    return {"message": "Facility level updated", "facility_level": req.facility_level}


# ─────────────────────────────────────────
# Post-Processing Functions
# ─────────────────────────────────────────

def _to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except (json.JSONDecodeError, TypeError):
            return {"raw": obj}
    return obj


SPECIALIST_KEYS = [
    ("cardiology_opinion", "Cardiology"),
    ("neurology_opinion", "Neurology"),
    ("pulmonology_opinion", "Pulmonology"),
    ("emergency_medicine_opinion", "Emergency Medicine"),
    ("general_medicine_opinion", "General Medicine"),
]


def compute_specialist_summaries(state: Dict) -> List[Dict]:
    summaries = []
    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if opinion:
            summaries.append({
                "specialty": opinion.get("specialty", name),
                "relevance_score": opinion.get("relevance_score", 0),
                "urgency_score": opinion.get("urgency_score", 0),
                "confidence": opinion.get("confidence", "LOW"),
                "one_liner": opinion.get("one_liner", ""),
                "claims_primary": opinion.get("claims_primary", False),
                "assessment": opinion.get("assessment", ""),
            })
    # Sort: primary claimant first, then by relevance desc, urgency as tiebreaker desc
    summaries.sort(key=lambda s: (s["claims_primary"], s["relevance_score"], s["urgency_score"]), reverse=True)
    return summaries


_WORKUP_NOISE = re.compile(
    r'\s*\([^)]*\)'                        # parenthetical suffixes: (Non-Contrast), (PA View)
    r'|\b(stat|urgent|routine|immediate'
    r'|serial|repeat|fasting|random'
    r'|non-contrast|with contrast|without contrast'
    r'|portable|bedside)\b',
    re.IGNORECASE,
)

def _workup_key(test_name: str) -> str:
    """Normalise a test name for deduplication. Strips qualifiers and parentheticals."""
    base = _WORKUP_NOISE.sub('', test_name)
    return re.sub(r'\s+', ' ', base).strip().lower()


def compute_consolidated_workup(state: Dict) -> List[Dict]:
    test_map: Dict[str, Dict] = {}
    priority_rank = {"STAT": 3, "URGENT": 2, "ROUTINE": 1}

    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        for item in opinion.get("recommended_workup", []):
            if isinstance(item, str):
                item = {"test": item, "priority": "ROUTINE", "rationale": ""}
            test_name = item.get("test", "").strip()
            if not test_name:
                continue
            dedup_key = _workup_key(test_name)
            item_priority = item.get("priority", "ROUTINE")
            if dedup_key in test_map:
                existing = test_map[dedup_key]
                if name not in existing["ordered_by"]:
                    existing["ordered_by"].append(name)
                # Keep highest priority; prefer shorter/cleaner display name
                if priority_rank.get(item_priority, 1) > priority_rank.get(existing["priority"], 1):
                    existing["priority"] = item_priority
                if len(test_name) < len(existing["test"]):
                    existing["test"] = test_name
            else:
                test_map[dedup_key] = {
                    "test": test_name,
                    "priority": item_priority,
                    "rationale": item.get("rationale", ""),
                    "ordered_by": [name],
                }

    priority_order = {"STAT": 0, "URGENT": 1, "ROUTINE": 2}
    return sorted(test_map.values(), key=lambda x: priority_order.get(x["priority"], 2))


def compute_safety_alerts(state: Dict) -> List[Dict]:
    alerts = []
    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        for flag in opinion.get("flags", []):
            if isinstance(flag, str):
                flag = {"severity": "INFO", "label": flag, "pattern": None}
            severity = flag.get("severity", "INFO")
            if severity in ("RED_FLAG", "YELLOW_FLAG"):
                alerts.append({
                    "severity": "CRITICAL" if severity == "RED_FLAG" else "WARNING",
                    "source": name,
                    "label": flag.get("label", ""),
                    "pattern": flag.get("pattern", ""),
                })
    return alerts


def compute_priority_score(classification_result: Dict, state: Dict) -> int:
    score, _ = _compute_priority_score_with_breakdown(classification_result, state)
    return score


def _compute_priority_score_with_breakdown(classification_result: Dict, state: Dict):
    """
    Evidence-based priority scoring rubric (max 100).

    Components grounded in NEWS2, ESI, MOHFW P1-P4, and NICE NG51:
      1. CMO Recommended Action  — 35 pts  (clinical synthesis, equiv. to ESI/MTS category)
      2. RED_FLAG count          — 25 pts  (NICE: ≥3 flags = NEWS2 ≥7 severity)
      3. Referral Urgency        — 20 pts  (MOHFW P1–P3 time windows)
      4. ML Risk Level + Adjust  — 12 pts  (ML has highest AUROC; override = clinical gestalt)
      5. Council Consensus       — 5 pts   (Split = unknown presentation = higher risk)
      6. YELLOW_FLAG count       — 3 pts   (small amplifier for borderline multi-system concern)
    """
    breakdown = {}

    # ── 1. CMO Recommended Action (max 35) ───────────────────────────────────
    cmo = _to_dict(state.get("cmo_verdict")) or {}
    action_pts = {"Immediate": 35, "Urgent": 24, "Standard": 12, "Can Wait": 3}
    action = cmo.get("recommended_action", "Standard")
    pts_action = action_pts.get(action, 12)
    breakdown["cmo_action"] = {"value": action, "points": pts_action, "max": 35}

    # ── 2. RED_FLAG count across all specialists (max 25) ────────────────────
    red_count = 0
    for key, _ in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        for flag in opinion.get("flags", []):
            if isinstance(flag, dict) and flag.get("severity") == "RED_FLAG":
                red_count += 1
    red_pts_map = {0: 0, 1: 8, 2: 16}
    pts_red = red_pts_map.get(red_count, 25)  # 3+ → 25
    breakdown["red_flags"] = {"value": red_count, "points": pts_red, "max": 25}

    # ── 3. Referral Urgency (max 20) ─────────────────────────────────────────
    urgency_pts = {"IMMEDIATE": 20, "WITHIN_1HR": 13, "WITHIN_4HRS": 6, "ELECTIVE": 0}
    referral_urgency = cmo.get("referral_urgency", "ELECTIVE")
    pts_urgency = urgency_pts.get(str(referral_urgency), 0)
    breakdown["referral_urgency"] = {"value": referral_urgency, "points": pts_urgency, "max": 20}

    # ── 4. ML Risk Level + CMO Override (max 12) ─────────────────────────────
    prediction = classification_result.get("prediction", {}) if isinstance(classification_result, dict) else {}
    ml_risk = prediction.get("risk_level", "Medium")
    risk_adjusted = bool(cmo.get("risk_adjusted", False))
    ml_base = {"High": 9, "Medium": 4, "Low": 0}
    pts_ml = ml_base.get(ml_risk, 4) + (4 if risk_adjusted else 0)
    pts_ml = min(pts_ml, 12)
    breakdown["ml_risk"] = {"value": f"{ml_risk}{'+ adjusted' if risk_adjusted else ''}", "points": pts_ml, "max": 12}

    # ── 5. Council Consensus (max 5) ─────────────────────────────────────────
    consensus_pts = {"Split": 5, "Majority": 2, "Unanimous": 0}
    consensus = cmo.get("council_consensus", "Unanimous")
    pts_consensus = consensus_pts.get(str(consensus), 0)
    breakdown["council_consensus"] = {"value": consensus, "points": pts_consensus, "max": 5}

    # ── 6. YELLOW_FLAG count (max 3) ─────────────────────────────────────────
    yellow_count = 0
    for key, _ in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        for flag in opinion.get("flags", []):
            if isinstance(flag, dict) and flag.get("severity") == "YELLOW_FLAG":
                yellow_count += 1
    yellow_pts = 3 if yellow_count >= 4 else (1 if yellow_count >= 2 else 0)
    breakdown["yellow_flags"] = {"value": yellow_count, "points": yellow_pts, "max": 3}

    total = pts_action + pts_red + pts_urgency + pts_ml + pts_consensus + yellow_pts
    total = min(100, max(1, total))

    # MOHFW priority label
    if total >= 75:
        label = "P1 — Immediate"
    elif total >= 50:
        label = "P2 — Urgent"
    elif total >= 25:
        label = "P3 — Semi-Urgent"
    else:
        label = "P4 — Non-Urgent"

    breakdown["total"] = total
    breakdown["label"] = label

    return total, breakdown


def compute_council_consensus(state: Dict, cmo_verdict: Dict) -> str:
    primary = cmo_verdict.get("primary_department", "")
    agree_count = 0
    total = 0
    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        total += 1
        if opinion.get("claims_primary"):
            rec = opinion.get("recommended_department", "")
            if rec and primary.lower() in rec.lower():
                agree_count += 1
            elif not rec:
                agree_count += 1
        else:
            agree_count += 1
    if total == 0:
        return "Unknown"
    ratio = agree_count / total
    if ratio >= 0.9:
        return "Unanimous"
    elif ratio >= 0.5:
        return "Majority"
    return "Split"


def compute_dissenting_opinions(state: Dict, cmo_verdict: Dict) -> List[Dict]:
    primary = cmo_verdict.get("primary_department", "").lower()
    dissenters = []
    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        if opinion.get("claims_primary"):
            rec = (opinion.get("recommended_department") or "").lower()
            if rec and primary not in rec and rec not in primary:
                dissenters.append({
                    "specialty": name,
                    "recommended": opinion.get("recommended_department"),
                    "relevance_score": opinion.get("relevance_score", 0),
                })
    return dissenters


def compute_key_factors(state: Dict, cmo_verdict: Dict) -> List[str]:
    factors = []
    explainability = cmo_verdict.get("explainability", {})
    if isinstance(explainability, dict):
        factors.extend(explainability.get("contributing_factors", []))

    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if not opinion:
            continue
        for flag in opinion.get("flags", []):
            if isinstance(flag, dict) and flag.get("severity") == "RED_FLAG":
                label = flag.get("label", "")
                if label and label not in factors:
                    factors.append(f"{name}: {label}")
    return factors[:10]


def compute_other_departments_flagged(state: Dict) -> List[Dict]:
    other = _to_dict(state.get("other_specialty_opinion"))
    if not other:
        return []
    flagged = []
    for dept in other.get("departments", []):
        if isinstance(dept, dict) and dept.get("relevance", 0) >= 3:
            flagged.append(dept)
    return sorted(flagged, key=lambda x: x.get("relevance", 0), reverse=True)


def enrich_verdict(state: Dict) -> Dict:
    cmo = _to_dict(state.get("cmo_verdict")) or {}
    classification = _to_dict(state.get("classification_result")) or {}

    # Carry facility_level into the enriched verdict for frontend use
    cmo["facility_level"] = state.get("facility_level", "District Hospital")

    # Store full specialist opinions so they survive DB round-trips
    full_opinions = []
    for key, name in SPECIALIST_KEYS:
        opinion = _to_dict(state.get(key))
        if opinion:
            full_opinions.append({"specialty": name, "data": opinion})
    cmo["full_specialist_opinions"] = full_opinions

    # Store other specialty opinion
    other = _to_dict(state.get("other_specialty_opinion"))
    if other:
        cmo["other_specialty_raw"] = other

    cmo["specialist_summaries"] = compute_specialist_summaries(state)
    cmo["consolidated_workup"] = compute_consolidated_workup(state)
    cmo["safety_alerts"] = compute_safety_alerts(state)
    priority_score, priority_breakdown = _compute_priority_score_with_breakdown(classification, state)
    cmo["priority_score"] = priority_score
    cmo["priority_breakdown"] = priority_breakdown
    cmo["council_consensus"] = compute_council_consensus(state, cmo)
    cmo["dissenting_opinions"] = compute_dissenting_opinions(state, cmo)
    cmo["key_factors"] = compute_key_factors(state, cmo)
    cmo["other_departments_flagged"] = compute_other_departments_flagged(state)

    prediction = classification.get("prediction", {}) if isinstance(classification, dict) else {}
    cmo["ml_risk_level"] = prediction.get("risk_level", "Unknown")

    explainability = cmo.get("explainability", {})
    if isinstance(explainability, dict):
        cmo["confidence"] = explainability.get("confidence_score", 0.5)
    else:
        cmo["confidence"] = 0.5

    return cmo


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def celsius_to_fahrenheit(c: float) -> float:
    return round((c * 9 / 5) + 32, 1)


async def ensure_session(user_id: str, session_id: str, initial_state: Dict):
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state=initial_state,
        )
    else:
        await session_service.update_session_state(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state=initial_state,
        )


# ─────────────────────────────────────────
# SSE Helper
# ─────────────────────────────────────────

def sse_event(event_type: str, data: Any) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


# ─────────────────────────────────────────
# Triage Endpoints
# ─────────────────────────────────────────

@app.post("/api/triage")
async def start_triage(patient: PatientData, doctor_id: int = Depends(get_current_doctor)):
    session_id = str(uuid.uuid4())
    user_id = f"user_{uuid.uuid4().hex[:8]}"

    patient_dict = patient.model_dump()
    patient_dict["temperature"] = celsius_to_fahrenheit(patient.temperature)

    # Compute BMI if weight and height provided
    if patient.weight_kg and patient.height_cm and patient.height_cm > 0:
        h_m = patient.height_cm / 100
        patient_dict["bmi"] = round(patient.weight_kg / (h_m ** 2), 1)

    initial_state = {
        "user_input": patient_dict,
        "facility_level": patient.facility_level or "District Hospital",
        "cardiology_opinion": "Not available",
        "neurology_opinion": "Not available",
        "pulmonology_opinion": "Not available",
        "emergency_medicine_opinion": "Not available",
        "general_medicine_opinion": "Not available",
        "other_specialty_opinion": "Not available",
    }

    await ensure_session(user_id, session_id, initial_state)

    in_time = datetime.now().isoformat()
    active_sessions[session_id] = {
        "user_id": user_id,
        "doctor_id": doctor_id,
        "patient_data": patient.model_dump(),  # Keep original Celsius for frontend
        "in_time": in_time,
        "status": "pending",
    }

    return {"session_id": session_id, "user_id": user_id}


@app.get("/api/triage/stream/{session_id}")
async def stream_triage(session_id: str, token: Optional[str] = None, doctor_id: Optional[int] = None):
    # EventSource can't send headers — accept token as query param too
    if doctor_id is None:
        if token is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        from auth import decode_token
        doctor_id = decode_token(token)
    session_info = active_sessions.get(session_id)
    if not session_info:
        raise HTTPException(status_code=404, detail="Session not found")
    if session_info["doctor_id"] != doctor_id:
        raise HTTPException(status_code=403, detail="Not your session")

    user_id = session_info["user_id"]

    async def event_generator():
        try:
            yield sse_event("status", {"message": "Pipeline started", "phase": "init"})

            content = types.Content(
                role="user",
                parts=[types.Part(text="START_TRIAGE")],
            )

            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
            ):
                author = event.author or ""
                text = ""
                if event.content and event.content.parts:
                    text = event.content.parts[0].text or ""

                yield sse_event("status", {
                    "message": f"{author}: processing",
                    "phase": author,
                    "text": text[:200] if text else "",
                })

            session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )

            if not session:
                yield sse_event("error", {"message": "Session lost"})
                return

            state = session.state

            classification = _to_dict(state.get("classification_result"))
            if classification:
                yield sse_event("classification_result", classification)

            for key, name in SPECIALIST_KEYS:
                opinion = _to_dict(state.get(key))
                if opinion:
                    yield sse_event("specialist_opinion", {
                        "specialty": name,
                        "data": opinion,
                    })

            other = _to_dict(state.get("other_specialty_opinion"))
            if other:
                yield sse_event("other_specialty_scores", other)

            enriched = enrich_verdict(state)
            yield sse_event("cmo_verdict", enriched)

            # Persist to SQLite
            timestamp = datetime.now().isoformat()
            db.save_patient(
                session_id=session_id,
                doctor_id=doctor_id,
                patient_data=session_info["patient_data"],
                classification=classification,
                verdict=enriched,
                timestamp=timestamp,
                in_time=session_info.get("in_time"),
            )
            active_sessions[session_id]["status"] = "completed"

            yield sse_event("complete", {"message": "Triage complete"})

        except Exception as e:
            yield sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────
# Dashboard Endpoints
# ─────────────────────────────────────────

@app.get("/api/dashboard/patients")
async def get_patients(doctor_id: int = Depends(get_current_doctor)):
    return db.get_patients_by_doctor(doctor_id)


@app.get("/api/dashboard/stats")
async def get_stats(doctor_id: int = Depends(get_current_doctor)):
    patients_store = db.get_patients_by_doctor(doctor_id)
    total = len(patients_store)
    if total == 0:
        return {
            "totalPatientsToday": 0,
            "highCriticalCount": 0,
            "avgPriorityScore": 0,
            "referralsMade": 0,
            "riskDistribution": {"Low": 0, "Medium": 0, "High": 0},
            "departmentLoad": {},
            "alertFrequency": {"CRITICAL": 0, "WARNING": 0},
        }

    risk_dist = {"Low": 0, "Medium": 0, "High": 0}
    dept_load: Dict[str, int] = {}
    alert_freq = {"CRITICAL": 0, "WARNING": 0}
    priority_sum = 0
    high_critical = 0
    referrals = 0

    for p in patients_store:
        verdict = p.get("verdict") or {}

        risk = verdict.get("final_risk_level", "Medium")
        risk_dist[risk] = risk_dist.get(risk, 0) + 1

        dept = verdict.get("primary_department", "Unknown")
        dept_load[dept] = dept_load.get(dept, 0) + 1

        priority_sum += verdict.get("priority_score", 50)

        visual = verdict.get("dashboard", {})
        if isinstance(visual, dict):
            vpl = visual.get("visual_priority_level", "")
            if vpl in ("HIGH", "CRITICAL"):
                high_critical += 1

        if verdict.get("referral_needed"):
            referrals += 1

        for alert in verdict.get("safety_alerts", []):
            sev = alert.get("severity", "")
            if sev in alert_freq:
                alert_freq[sev] += 1

    return {
        "totalPatientsToday": total,
        "highCriticalCount": high_critical,
        "avgPriorityScore": round(priority_sum / total) if total else 0,
        "referralsMade": referrals,
        "riskDistribution": risk_dist,
        "departmentLoad": dept_load,
        "alertFrequency": alert_freq,
    }


@app.delete("/api/patients/{session_id}")
async def discharge_patient(session_id: str, doctor_id: int = Depends(get_current_doctor)):
    success = db.discharge_patient(session_id, doctor_id)
    if not success:
        raise HTTPException(status_code=404, detail="Patient not found or already discharged")
    return {"message": "Patient discharged"}


@app.get("/api/patients/{session_id}/notes")
async def get_notes(session_id: str, doctor_id: int = Depends(get_current_doctor)):
    patient = db.get_patient_by_session(session_id, doctor_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient["doctor_notes"] or {}


@app.post("/api/patients/{session_id}/notes")
async def save_notes(session_id: str, req: DoctorNotesRequest, doctor_id: int = Depends(get_current_doctor)):
    notes = {
        "doctor_name": req.doctor_name,
        "clinical_impression": req.clinical_impression,
        "suggestions": req.suggestions,
        "saved_at": datetime.now().isoformat(),
    }
    ok = db.save_doctor_notes(session_id, doctor_id, notes)
    if not ok:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"message": "Notes saved"}


@app.get("/api/patients/{session_id}/report.pdf")
async def download_report(session_id: str, doctor_id: int = Depends(get_current_doctor)):
    from services.pdf_generator import generate_pdf
    patient = db.get_patient_by_session(session_id, doctor_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    pdf_bytes = generate_pdf(
        patient_data=patient["patient_data"],
        classification=patient["classification"] or {},
        verdict=patient["verdict"] or {},
        doctor_notes=patient["doctor_notes"],
        in_time=patient.get("in_time"),
    )
    name = (patient["patient_data"].get("name") or "patient").replace(" ", "_")
    filename = f"triage_{name}_{session_id[:8]}.pdf"
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────
# Document Upload
# ─────────────────────────────────────────

@app.post("/api/upload/document")
async def upload_document(file: UploadFile = File(...)):
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    return {"filename": file.filename, "text": text[:5000]}


# ─────────────────────────────────────────
# Legacy endpoint (backwards compat)
# ─────────────────────────────────────────

class RunRequest(BaseModel):
    user_id: str
    session_id: str
    patient_data: PatientData


@app.post("/run/stream")
async def run_agent_stream(req: RunRequest):
    async def event_generator():
        try:
            initial_state = {"user_input": req.patient_data.model_dump()}
            await ensure_session(req.user_id, req.session_id, initial_state)

            content = types.Content(
                role="user",
                parts=[types.Part(text="START_TRIAGE")],
            )

            async for event in runner.run_async(
                user_id=req.user_id,
                session_id=req.session_id,
                new_message=content,
            ):
                payload = {
                    "author": event.author,
                    "is_final": event.is_final_response(),
                    "kind": "text",
                }
                if event.content and event.content.parts:
                    raw_text = event.content.parts[0].text
                    payload["text"] = raw_text
                    if raw_text and raw_text.strip().startswith("{"):
                        payload["kind"] = "structured"

                yield f"data: {json.dumps(payload)}\n\n"

            session = await session_service.get_session(
                app_name=APP_NAME,
                user_id=req.user_id,
                session_id=req.session_id,
            )
            verdict = session.state.get("cmo_verdict")
            if verdict:
                if hasattr(verdict, "model_dump"):
                    verdict = verdict.model_dump()
                yield f"data: {json.dumps({'kind': 'cmo_verdict', 'data': verdict, 'is_final': True})}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")



# ─────────────────────────────────────────
# Local Run
# ─────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
