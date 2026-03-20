import os
import json
import uuid
import httpx
import fitz  # PyMuPDF
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import Response
from twilio.twiml.messaging_response import MessagingResponse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent

# ─────────────────────────────────────────
# Setup
# ─────────────────────────────────────────

load_dotenv()
app = FastAPI(title="TriageAI WhatsApp Webhook")
APP_NAME = "triage_app"
session_service = InMemorySessionService()

runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)

# ─────────────────────────────────────────
# PDF Parsing Logic
# ─────────────────────────────────────────

async def extract_text_from_pdf_url(url: str) -> str:
    """Downloads a PDF from Twilio and extracts text."""
    async with httpx.AsyncClient() as client:
        # Twilio Media URLs are publicly accessible but expire
        response = await client.get(url)
        if response.status_code != 200:
            return "Error: Could not download PDF."
        
        # Open PDF from memory
        doc = fitz.open(stream=response.content, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        return text.strip()

# ─────────────────────────────────────────
# Formatting Helpers (Keeping your logic)
# ─────────────────────────────────────────

def build_triage_header(classification):
    patient_name = classification.get("name", classification.get("patient_name", "Unknown"))
    prediction = classification.get("prediction", {})
    risk_level = prediction.get("risk_level", "Unknown")
    confidence = prediction.get("max_confidence", 0)
    risk_emoji = "🟠" if risk_level == "High" else "🟡" if risk_level == "Medium" else "🟢"
    return (f"🏥 *Ydhya TRIAGE RESULT*\n━━━━━━━━━━━━━━\n👤 {patient_name}\n"
            f"{risk_emoji} Risk Level: {risk_level}\n📊 Confidence Score: {confidence}%\n\n")

def format_cmo_section(cmo):

    final_risk = cmo["final_risk_level"]
    primary_dept = cmo.get("primary_department", "General Medicine")
    secondary_dept = cmo.get("secondary_department")
    explanation = cmo.get("explanation", "")
    recommended_action = cmo.get("recommended_action", "Standard")

    confidence = round(cmo["explainability"]["confidence_score"] * 100, 1)
    factors = cmo["explainability"].get("contributing_factors", [])

    risk_emoji = (
        "🟠" if final_risk == "High"
        else "🟡" if final_risk == "Medium"
        else "🟢"
    )

    secondary_line = ""
    if secondary_dept:
        secondary_line = f"Secondary: {secondary_dept}\n"

    factors_block = ""
    if factors:
        formatted = "\n".join([f"• {f}" for f in factors])
        factors_block = f"\n🔎 *Key Clinical Factors*\n{formatted}\n"

    referral_block = ""
    if cmo.get("referral_needed"):
        referral_details = cmo.get(
            "referral_details",
            "Higher centre evaluation advised"
        )
        referral_block = f"\n🚑 *Referral Advice*\n{referral_details}\n"

    message = (
        f"📊 *Final Clinical Verdict on Risk Level*\n"
        f"{risk_emoji} {final_risk}\n\n"

        f"🏥 *Department Recommendation*\n"
        f"Primary: {primary_dept}\n"
        f"{secondary_line}"
    )

    message += factors_block
    message += referral_block

    message += (
        f"\n📈 *CMO Confidence*\n"
        f"{confidence}%\n\n"

        f"🧾 *Clinical Explanation*\n"
        f"{explanation}\n\n"

        f"✅ *Recommended Action:* {recommended_action}\n"
    )

    message += (
        "\nℹ️ *Important:* This is an AI-assisted triage support system "
        "and does not replace a doctor's examination."
    )

    return message

# ─────────────────────────────────────────
# WhatsApp Webhook
# ─────────────────────────────────────────

@app.post("/whatsapp/callback")
async def whatsapp_callback(request: Request):
    form_data = await request.form()
    
    # Twilio Parameters
    incoming_msg = form_data.get("Body", "")
    from_number = form_data.get("From")
    media_url = form_data.get("MediaUrl0")
    content_type = form_data.get("MediaContentType0")

    twilio_response = MessagingResponse()

    # 1. PROCESS INPUT (TEXT OR PDF)
    final_input_text = ""

    try:
        if media_url and "application/pdf" in content_type:
            # If user sent a PDF, extract its content
            print(f"📎 Processing PDF from {from_number}...")
            final_input_text = await extract_text_from_pdf_url(media_url)
        else:
            # Otherwise use the raw text message
            final_input_text = incoming_msg

        if not final_input_text:
            return Response(content=str(twilio_response.message("No readable text or PDF found.")), media_type="application/xml")

        # 2. SESSION SETUP
        user_id = from_number
        session_id = f"{from_number}_{uuid.uuid4().hex}"

        # Send the extracted/raw text to the root agent
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state={
                "user_input": final_input_text,
                "facility_level": "District Hospital",
                "classification_result": {},
                "cardiology_opinion": "Not available",
                "neurology_opinion": "Not available",
                "pulmonology_opinion": "Not available",
                "emergency_medicine_opinion": "Not available",
                "general_medicine_opinion": "Not available",
                "other_specialty_opinion": "Not available",
            }
        )

        # 3. TRIGGER AGENT RUN
        content = types.Content(role="user", parts=[types.Part(text="PROCESS_NEW_PATIENT")])
        
        classification_result = None
        cmo_verdict = None

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            if not event.content or not event.content.parts:
                continue

            text = event.content.parts[0].text.strip()
            # Clean possible markdown formatting
            clean_text = text.replace("```json", "").replace("```", "").strip()

            if "Classification" in event.author and clean_text.startswith("{"):
                classification_result = json.loads(clean_text)

            if "ChiefMedicalOfficer" in event.author and clean_text.startswith("{"):
                cmo_verdict = json.loads(clean_text)

        # 4. FINAL RESPONSE
        if classification_result and cmo_verdict:
            header = build_triage_header(classification_result)
            cmo_body = format_cmo_section(cmo_verdict)
            twilio_response.message(header + cmo_body)
        else:
            # Note: This usually triggers if the agent chain didn't finish or author names don't match
            twilio_response.message("⚠️ The medical council is still deliberating. Please wait.")

    except Exception as e:
        print(f"Server Error: {e}")
        twilio_response.message("❌ System Error: Could not process medical records.")

    return Response(content=str(twilio_response), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)