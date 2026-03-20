"""
TriageAI — Classification Agent
Location: backend/app/sub_agents/ClassificationAgent/agent.py

Reads patient data from state["user_input"]
Runs XGBoost prediction (pure code, NO LLM)
Saves results to state["classification_result"]
"""

import pickle
import logging
import pandas as pd
import json
from pathlib import Path
from typing import AsyncGenerator
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

logger = logging.getLogger(__name__)

# ============================================================
# MODEL PATHS
# ============================================================

AGENT_DIR = Path(__file__).parent
MODEL_PATH = (AGENT_DIR / ".." / ".." / ".." / "model" / "model.pkl").resolve()
ENCODER_PATH = (AGENT_DIR / ".." / ".." / ".." / "model" / "label_encoder.pkl").resolve()

# ============================================================
# FEATURES (MUST MATCH TRAINING)
# ============================================================

ALL_SYMPTOMS = [
    "chest_pain", "breathlessness", "headache", "fever", "cough",
    "abdominal_pain", "nausea", "vomiting", "dizziness", "fatigue",
    "palpitations", "back_pain", "joint_pain", "diarrhea", "sore_throat",
    "body_ache", "weakness", "blurred_vision", "numbness", "confusion",
    "seizures", "blood_in_stool", "weight_loss", "sweating", "swelling",
    "burning_urination", "rash", "cold", "wheezing", "loss_of_appetite"
]

ALL_CONDITIONS = [
    "diabetes", "hypertension", "asthma", "copd", "heart_disease",
    "kidney_disease", "liver_disease", "thyroid", "tuberculosis",
    "cancer", "hiv", "anemia", "obesity"
]


class ClassificationAgentImpl(BaseAgent):
    """
    Pure code agent — XGBoost classification.

    Reads:  state["user_input"]
    Writes: state["classification_result"]
    """

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str):
        super().__init__(name=name, sub_agents=[])
        self._model = None
        self._label_encoder = None
        self._load_model()

    # ============================================================
    # MODEL LOADING
    # ============================================================

    def _load_model(self):
        try:
            with open(MODEL_PATH, "rb") as f:
                self._model = pickle.load(f)

            with open(ENCODER_PATH, "rb") as f:
                self._label_encoder = pickle.load(f)

            logger.info(f"[{self.name}] ✅ Model loaded")

        except Exception as e:
            logger.error(f"[{self.name}] ❌ Model load failed: {e}")

    # ============================================================
    # INPUT VALIDATION
    # ============================================================

    def _validate_input(self, user_input: dict):

        REQUIRED_FIELDS = [
            "age", "gender",
            "bp_systolic", "bp_diastolic",
            "heart_rate", "temperature", "spo2"
        ]

        missing = [f for f in REQUIRED_FIELDS if f not in user_input]

        if missing:
            raise ValueError(f"Missing required fields: {missing}")

    # ============================================================
    # BUILD MODEL INPUT
    # ============================================================

    @staticmethod
    def _normalize(s: str) -> str:
        """Lowercase and replace spaces/hyphens with underscores for model key matching."""
        return s.lower().replace(" ", "_").replace("-", "_")

    def _build_model_input(self, user_input: dict) -> pd.DataFrame:

        raw_symptoms = user_input.get("symptoms", [])
        raw_conditions = user_input.get("conditions", [])
        gender_str = user_input.get("gender", "Male")

        # Normalize to underscore format so "chest pain" matches "chest_pain"
        symptoms = [self._normalize(s) for s in raw_symptoms]
        conditions = [self._normalize(c) for c in raw_conditions]

        row = {
            "age": user_input["age"],
            "gender": 0 if gender_str.lower() == "male" else 1,
        }

        for s in ALL_SYMPTOMS:
            row[f"symptom_{s}"] = int(s in symptoms)

        row["bp_systolic"] = user_input["bp_systolic"]
        row["bp_diastolic"] = user_input["bp_diastolic"]
        row["heart_rate"] = user_input["heart_rate"]
        row["temperature"] = user_input["temperature"]
        row["spo2"] = user_input["spo2"]

        for c in ALL_CONDITIONS:
            row[f"condition_{c}"] = int(c in conditions)

        row["has_pre_existing"] = int(len(conditions) > 0)
        row["num_symptoms"] = len(raw_symptoms)
        row["num_conditions"] = len(raw_conditions)

        return pd.DataFrame([row])

    # ============================================================
    # PREDICTION
    # ============================================================

    def _predict(self, df: pd.DataFrame) -> dict:

        risk_code = self._model.predict(df)[0]
        risk_label = self._label_encoder.inverse_transform([risk_code])[0]
        probabilities = self._model.predict_proba(df)[0]

        confidence = {
            str(cls): round(float(prob) * 100, 1)
            for cls, prob in zip(self._label_encoder.classes_, probabilities)
        }

        return {
            "risk_level": risk_label,
            "risk_code": int(risk_code),
            "confidence": confidence,
            "max_confidence": round(float(max(probabilities)) * 100, 1),
        }

    # ============================================================
    # DERIVED METRICS
    # ============================================================

    def _compute_vital_severity(self, user_input: dict) -> dict:

        bp_sys = user_input.get("bp_systolic", 120)
        bp_dia = user_input.get("bp_diastolic", 80)
        hr = user_input.get("heart_rate", 75)
        temp = user_input.get("temperature", 98.6)
        spo2 = user_input.get("spo2", 97)

        score = 0

        if bp_sys >= 180 or bp_dia >= 120: score += 3
        elif bp_sys >= 160 or bp_dia >= 100: score += 2
        elif bp_sys >= 140 or bp_dia >= 90: score += 1
        elif bp_sys < 90: score += 3

        if hr > 130 or hr < 50: score += 3
        elif hr > 110 or hr < 55: score += 2
        elif hr > 100: score += 1

        if temp >= 104.0: score += 3
        elif temp >= 102.0: score += 2
        elif temp >= 100.4: score += 1

        if spo2 < 85: score += 4
        elif spo2 < 90: score += 3
        elif spo2 < 94: score += 2
        elif spo2 < 96: score += 1

        conditions = user_input.get("conditions", [])

        severe = {"heart_disease", "cancer", "kidney_disease", "hiv"}
        moderate = {"diabetes", "hypertension", "copd", "liver_disease"}

        comorbidity = 0
        for c in conditions:
            if c in severe: comorbidity += 2
            elif c in moderate: comorbidity += 1

        return {
            "vital_severity_score": score,
            "vital_severity_level": (
                "critical" if score >= 8 else
                "elevated" if score >= 4 else
                "normal"
            ),
            "comorbidity_risk_score": comorbidity,
            "comorbidity_level": (
                "high" if comorbidity >= 3 else
                "moderate" if comorbidity >= 1 else
                "none"
            ),
        }

    # ============================================================
    # DEBUG PRINT
    # ============================================================

    def _debug_print(self, result: dict):

        print("\n" + "=" * 60)
        print("🏥 CLASSIFICATION RESULT")
        print("=" * 60)

        print(f"Patient      : {result['patient_name']} ({result['patient_id']})")
        print(f"Age / Gender : {result['age']} / {result['gender']}")
        print(f"Symptoms     : {', '.join(result['symptoms'])}")
        print(f"Conditions   : {', '.join(result['conditions']) or 'None'}")

        v = result["vitals"]
        print(
            f"Vitals       : BP={v['bp_systolic']}/{v['bp_diastolic']} | "
            f"HR={v['heart_rate']} | Temp={v['temperature']}°F | SpO₂={v['spo2']}%"
        )

        p = result["prediction"]
        print("\n🚦 Risk Level :", p["risk_level"])
        print(
            f"📊 Confidence : Low={p['confidence']['Low']}% | "
            f"Medium={p['confidence']['Medium']}% | "
            f"High={p['confidence']['High']}%"
        )

        d = result["derived_metrics"]
        print(f"⚡ Vital Severity : {d['vital_severity_level']} ({d['vital_severity_score']})")
        print(f"💊 Comorbidity    : {d['comorbidity_level']} ({d['comorbidity_risk_score']})")

        print("=" * 60)

        print("\n=== RAW classification_result JSON ===")
        print(json.dumps(result, indent=2))

        print("=" * 60 + "\n")

    # ============================================================
    # MAIN EXECUTION
    # ============================================================

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        # raw_data is IngestAgent's structured output; fall back to user_input if it failed
        user_input = ctx.session.state.get("raw_data") or ctx.session.state.get("user_input")

        if not user_input:
            print("\n❌ ERROR: raw_data and user_input both missing in session state\n")
            return

        # Pydantic model → dict so .get() works regardless of how ADK stored it
        if hasattr(user_input, "model_dump"):
            user_input = user_input.model_dump()

        # WhatsApp sends user_input as plain string — not usable for XGBoost
        if isinstance(user_input, str):
            print("\n❌ ERROR: user_input is a plain string; IngestAgent likely failed to produce raw_data\n")
            return

        if not self._model or not self._label_encoder:
            print("\n❌ ERROR: Model artifacts not loaded\n")
            return

        patient_name = user_input.get("name", "Unknown")
        patient_id = user_input.get("patient_id", "N/A")

        yield Event(
            author=self.name,
            content=types.Content(
                role="assistant",
                parts=[types.Part(text=f"[{self.name}] 🔄 Classifying {patient_name}...")]
            ),
        )

        try:
            self._validate_input(user_input)

            df = self._build_model_input(user_input)
            prediction = self._predict(df)
            derived_metrics = self._compute_vital_severity(user_input)

        except Exception as e:
            print(f"\n❌ CLASSIFICATION ERROR: {e}\n")
            return

        classification_result = {
            "patient_id": patient_id,
            "patient_name": patient_name,
            "age": user_input.get("age"),
            "gender": user_input.get("gender"),
            "symptoms": user_input.get("symptoms", []),
            "conditions": user_input.get("conditions", []),
            "vitals": {
                "bp_systolic": user_input.get("bp_systolic"),
                "bp_diastolic": user_input.get("bp_diastolic"),
                "heart_rate": user_input.get("heart_rate"),
                "temperature": user_input.get("temperature"),
                "spo2": user_input.get("spo2"),
            },
            "anthropometry": {
                "weight_kg": user_input.get("weight_kg"),
                "height_cm": user_input.get("height_cm"),
                "bmi": user_input.get("bmi"),
            },
            "additional_info": user_input.get("additional_info"),
            "prediction": prediction,
            "derived_metrics": derived_metrics,
        }

        ctx.session.state["classification_result"] = classification_result

        self._debug_print(classification_result) 

        # 🧾 Human-readable ML summary (NO hallucination)
        risk_level = prediction["risk_level"]
        max_conf = prediction["max_confidence"]

        summary_text = (
            f"🚦 RISK LEVEL: {risk_level}\n"
            f"Confidence: {max_conf}%"
        )

        # 1️⃣ Emit readable summary first
        yield Event(
            author=self.name,
            content=types.Content(
                role="assistant",
                parts=[types.Part(text=summary_text)],
            ),
        )


        # 1️⃣ Emit structured JSON event (THIS is the key upgrade)
        yield Event(
            author=self.name,
            content=types.Content(
                role="assistant",
                parts=[
                    types.Part(
                        text=json.dumps(classification_result)
                    )
                ],
            ),
        )

        # 2️⃣ Emit completion message (optional but nice)
        yield Event(
            author=self.name,
            content=types.Content(
                role="assistant",
                parts=[
                    types.Part(
                        text=f"[{self.name}] ✅ Classification Complete"
                    )
                ],
            ),
        )



# ============================================================
# EXPORT
# ============================================================

ClassificationAgent = ClassificationAgentImpl(name="ClassificationAgent")
