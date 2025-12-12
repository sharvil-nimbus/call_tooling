import os
import json
import requests
from flask import Flask, request, jsonify
import openai

app = Flask(__name__)

DIALPAD_API_KEY = os.getenv("DIALPAD_API_KEY")
DIALPAD_PHONE   = os.getenv("DIALPAD_PHONE")
openai.api_key  = os.getenv("OPENAI_API_KEY")

conversation_state = {}

def send_sms(to: str, text: str):
    requests.post(
        "https://api.dialpad.com/api/v2/sms",
        headers={"Authorization": f"Bearer {DIALPAD_API_KEY}"},
        json={"phone_number": DIALPAD_PHONE, "to_numbers": [to], "message": text}
    ).raise_for_status()

def send_initial_reminder(patient_phone: str, patient_name: str = ""):
    msg = f"Hey{(' ' + patient_name) if patient_name else ''}, it’s 7 days before your visit with Dr Hendricks and he ordered a CT. Did you get it done yet?"
    send_sms(patient_phone, msg)
    conversation_state[patient_phone] = {"stage": "awaiting_yes_no"}

def handle_inbound(patient_phone: str, text: str):
    state = conversation_state.get(patient_phone, {})
    stage = state.get("stage")

    if stage == "awaiting_yes_no":
        # Yes or No?
        classification = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[{"role": "system", "content": "Answer only 'yes' or 'no' in lowercase."},
                      {"role": "user", "content": text}]
        ).choices[0].message.content.strip()

        if "yes" in classification:
            send_sms(patient_phone, "Great! Can you tell me where and on what date you had the CT done?")
            state["stage"] = "awaiting_ct_details"
        else:
            send_sms(patient_phone, "No problem. May I ask why you haven’t been able to get it done yet?")
            state["stage"] = "awaiting_no_reason"

    elif stage == "awaiting_no_reason":
        result = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{
                "role": "system",
                "content": """Classify into exactly one category. Return only JSON:
{
  "category": "doesnt_want" | "forgot" | "cant_remember" | "pushed_back",
  "new_date": "YYYY-MM-DD"   // only if mentioned, otherwise null
}"""
            }, {"role": "user", "content": text}]
        ).choices[0].message.content

        data = json.loads(result)
        cat = data["category"]

        if cat == "doesnt_want":
            send_sms(patient_phone, "I understand. Would it be okay if someone from the office gives you a quick call to discuss?")
        elif cat == "forgot":
            send_sms(patient_phone, "That happens! Do you need help scheduling it or finding a convenient location?")
        elif cat == "cant_remember":
            send_sms(patient_phone, "No worries at all — we’ll check with the imaging centers and get back to you today.")
        elif cat == "pushed_back":
            new_date = data.get("new_date")
            if new_date:
                send_sms(patient_phone, f"Thanks! You mentioned {new_date} — is that the new date?")
            else:
                send_sms(patient_phone, "Got it, you had to push it back. When is the new appointment scheduled for?")

        if cat != "pushed_back":
            conversation_state.pop(patient_phone, None)

    conversation_state[patient_phone] = state

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data.get("event") == "sms.inbound_sms":
        patient_phone = data["from"]
        text = data["text"].strip()
        handle_inbound(patient_phone, text)
    return jsonify({"status": "ok"})

@app.post("/remind")
def remind():
    phone = request.json["phone"]
    name = request.json.get("name", "")
    send_initial_reminder(phone, name)
    return "sent"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
