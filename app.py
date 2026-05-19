import json
import os
from contextlib import closing
from datetime import date, datetime, timedelta

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from openai import OpenAI
from twilio.twiml.voice_response import Gather, VoiceResponse

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
schema_checked = False
call_sessions = {}
VOICE_FIELDS = [
    "patient_name",
    "patient_email",
    "symptoms",
    "appointment_date",
    "appointment_time",
    "doctor_id",
]
FIELD_PROMPTS = {
    "patient_name": "Please tell me your full name.",
    "patient_email": "Please say your email address slowly, or say skip email for now.",
    "symptoms": "Please describe your symptoms briefly.",
    "appointment_date": "Which date would you like? You can say tomorrow, today, or a date like 2026 dash 05 dash 20.",
    "appointment_time": "Which available time slot would you like?",
    "doctor_id": "Which doctor would you prefer, or should I recommend one?",
}


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "hospital_ai_booking"),
    )


def fetch_all(query, params=None):
    with closing(get_db_connection()) as connection:
        with closing(connection.cursor(dictionary=True)) as cursor:
            cursor.execute(query, params or ())
            return cursor.fetchall()


def execute_query(query, params=None):
    with closing(get_db_connection()) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(query, params or ())
            connection.commit()
            return cursor.lastrowid


def xml_response(twiml):
    return Response(str(twiml), mimetype="text/xml")


def voice_gather(prompt, action="/voice/respond"):
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=action,
        method="POST",
        speech_timeout="auto",
        timeout=5,
        language="en-IN",
    )
    gather.say(prompt, voice="alice", language="en-IN")
    response.append(gather)
    response.say("I did not hear anything. Let us try again.", voice="alice", language="en-IN")
    response.redirect(action, method="POST")
    return xml_response(response)


def voice_say_and_hangup(message):
    response = VoiceResponse()
    response.say(message, voice="alice", language="en-IN")
    response.hangup()
    return xml_response(response)


def ensure_schema_updates():
    global schema_checked
    if schema_checked:
        return

    try:
        with closing(get_db_connection()) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    """
                    ALTER TABLE appointments
                    ADD COLUMN appointment_time VARCHAR(20) NULL
                    AFTER appointment_date
                    """
                )
                connection.commit()
    except mysql.connector.Error as error:
        if error.errno != 1060:
            raise
    schema_checked = True


def get_doctors():
    return fetch_all(
        """
        SELECT id, name, specialty, qualification, experience_years,
               available_days, available_time
        FROM doctors
        ORDER BY specialty, name
        """
    )


def get_doctor(doctor_id):
    rows = fetch_all(
        """
        SELECT id, name, specialty, qualification, experience_years,
               available_days, available_time
        FROM doctors
        WHERE id = %s
        """,
        (doctor_id,),
    )
    return rows[0] if rows else None


def parse_time_label(label):
    return datetime.strptime(label.strip(), "%I:%M %p")


def generate_time_slots(available_time):
    start_text, end_text = available_time.split(" - ", 1)
    current = parse_time_label(start_text)
    end = parse_time_label(end_text)
    slots = []

    while current < end:
        slots.append(current.strftime("%I:%M %p").lstrip("0"))
        current += timedelta(minutes=30)

    return slots


def doctor_available_on(doctor, appointment_date):
    day_name = datetime.strptime(appointment_date, "%Y-%m-%d").strftime("%A")
    available_days = doctor["available_days"].lower()

    if "monday to saturday" in available_days:
        return day_name != "Sunday"

    return day_name.lower() in available_days


def get_booked_slots(doctor_id, appointment_date):
    rows = fetch_all(
        """
        SELECT appointment_time
        FROM appointments
        WHERE doctor_id = %s
          AND appointment_date = %s
          AND appointment_time IS NOT NULL
          AND status = 'Booked'
        """,
        (doctor_id, appointment_date),
    )
    return {row["appointment_time"] for row in rows}


def get_available_slots(doctor_id, appointment_date):
    doctor = get_doctor(doctor_id)
    if not doctor:
        return []

    if not doctor_available_on(doctor, appointment_date):
        return []

    all_slots = generate_time_slots(doctor["available_time"])
    booked_slots = get_booked_slots(doctor_id, appointment_date)
    return [slot for slot in all_slots if slot not in booked_slots]


def local_specialty_match(symptoms):
    text = symptoms.lower()
    rules = {
        "Cardiology": ["chest", "heart", "bp", "blood pressure", "palpitation"],
        "Dermatology": ["skin", "rash", "itch", "acne", "allergy"],
        "Neurology": ["headache", "migraine", "seizure", "numb", "dizzy"],
        "Orthopedics": ["bone", "joint", "back pain", "fracture", "knee"],
        "Pediatrics": ["child", "baby", "infant", "kid", "fever in child"],
    }
    for specialty, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            return specialty
    return "General Medicine"


def has_emergency_signs(symptoms):
    text = symptoms.lower()
    emergency_keywords = [
        "severe chest pain",
        "breathing difficulty",
        "shortness of breath",
        "fainting",
        "unconscious",
        "stroke",
        "heavy bleeding",
        "suicidal",
        "cannot breathe",
    ]
    return any(keyword in text for keyword in emergency_keywords)


def parse_ai_json(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()
    return json.loads(cleaned)


def normalize_spoken_date(text):
    if not text:
        return ""

    spoken = text.lower().strip()
    today = date.today()
    if "today" in spoken:
        return today.isoformat()
    if "tomorrow" in spoken:
        return (today + timedelta(days=1)).isoformat()

    cleaned = (
        spoken.replace(" dash ", "-")
        .replace(" slash ", "/")
        .replace(" hyphen ", "-")
        .replace(" ", "")
    )
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def normalize_spoken_time(text, slots):
    if not text:
        return ""

    spoken = text.lower().replace(".", "").strip()
    spoken = spoken.replace("a m", "am").replace("p m", "pm")
    spoken = spoken.replace("a.m", "am").replace("p.m", "pm")

    for slot in slots:
        if slot.lower().replace(" ", "") in spoken.replace(" ", ""):
            return slot

    if "morning" in spoken and slots:
        return slots[0]
    if "afternoon" in spoken and slots:
        return slots[len(slots) // 2]

    return ""


def find_doctor_from_text(text, doctors):
    spoken = text.lower()
    for doctor in doctors:
        name_parts = doctor["name"].lower().replace("dr.", "").split()
        if doctor["specialty"].lower() in spoken:
            return doctor
        if any(part and part in spoken for part in name_parts):
            return doctor
    return None


def make_placeholder_email(phone):
    digits = "".join(character for character in phone if character.isdigit())
    return f"caller{digits or 'unknown'}@phone.local"


def get_voice_session(call_sid, caller_phone):
    if call_sid not in call_sessions:
        call_sessions[call_sid] = {
            "patient_name": "",
            "patient_email": "",
            "patient_phone": caller_phone or "",
            "symptoms": "",
            "appointment_date": "",
            "appointment_time": "",
            "doctor_id": "",
            "last_prompt": "symptoms",
            "confirmed": False,
        }
    return call_sessions[call_sid]


def missing_voice_fields(session):
    missing = []
    for field in VOICE_FIELDS:
        if not str(session.get(field, "")).strip():
            missing.append(field)
    return missing


def available_slot_text(doctor_id, appointment_date):
    if not doctor_id or not appointment_date:
        return ""
    slots = get_available_slots(int(doctor_id), appointment_date)
    if not slots:
        return "No open slots are available for that doctor on that date."
    return "Available slots are " + ", ".join(slots[:6]) + "."


def build_booking_summary(session):
    doctor = get_doctor(int(session["doctor_id"]))
    return (
        f"I have {session['patient_name']} for {doctor['name']}, "
        f"{doctor['specialty']}, on {session['appointment_date']} at "
        f"{session['appointment_time']}. Say confirm booking to book it, "
        "or say change details."
    )


def ai_recommendation(symptoms, doctors):
    doctor_context = "\n".join(
        f"{doctor['id']}. {doctor['name']} - {doctor['specialty']}, "
        f"{doctor['experience_years']} years, available {doctor['available_days']} "
        f"({doctor['available_time']})"
        for doctor in doctors
    )

    prompt = f"""
You are an appointment assistant for a hospital. Recommend the most suitable doctor
from the list using the patient's symptoms. Do not diagnose. Tell the patient to seek
emergency care for severe symptoms such as chest pain, breathing trouble, fainting,
stroke symptoms, or heavy bleeding.

Patient symptoms:
{symptoms}

Available doctors:
{doctor_context}

Return only valid JSON with these keys:
recommended_doctor_id, specialty, reason, confidence, questions_to_ask,
preparation_tips, safety_note, urgency
"""

    response = openai_client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
    )
    return parse_ai_json(response.output_text)


def fallback_recommendation(symptoms, doctors):
    specialty = local_specialty_match(symptoms)
    matched_doctor = next(
        (doctor for doctor in doctors if doctor["specialty"] == specialty),
        doctors[0],
    )
    urgent = has_emergency_signs(symptoms)
    return {
        "recommended_doctor_id": matched_doctor["id"],
        "specialty": matched_doctor["specialty"],
        "reason": "Recommended using local symptom matching because AI is unavailable.",
        "confidence": "medium",
        "questions_to_ask": [
            "How long have you had these symptoms?",
            "Are the symptoms getting worse?",
            "Do you have fever, severe pain, or breathing difficulty?",
        ],
        "preparation_tips": [
            "Carry previous medical records and current medicine details.",
            "Reach 10 minutes before the appointment time.",
        ],
        "safety_note": "Seek urgent care now if symptoms are severe or worsening.",
        "urgency": "emergency" if urgent else "routine",
    }


def ai_chat_reply(message, doctors, context):
    doctor_context = "\n".join(
        f"{doctor['id']}. {doctor['name']} - {doctor['specialty']}, "
        f"{doctor['qualification']}, {doctor['experience_years']} years, "
        f"{doctor['available_days']} ({doctor['available_time']})"
        for doctor in doctors
    )

    prompt = f"""
You are a safe hospital appointment booking assistant. Help patients choose a
doctor, prepare for an appointment, and understand the booking process.
Do not provide a medical diagnosis or medicine dosage. If emergency symptoms are
mentioned, advise immediate emergency care.

Today is {date.today().isoformat()}.
Current patient/form context:
{json.dumps(context, indent=2)}

Available doctors:
{doctor_context}

Patient message:
{message}

Return only valid JSON with these keys:
reply, suggested_doctor_id, requested_date, requested_time, urgency,
next_action, should_book, missing_fields
"""

    response = openai_client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
    )
    return parse_ai_json(response.output_text)


def ai_voice_update(message, doctors, session):
    doctor_context = "\n".join(
        f"{doctor['id']}. {doctor['name']} - {doctor['specialty']}, "
        f"available {doctor['available_days']} ({doctor['available_time']})"
        for doctor in doctors
    )

    prompt = f"""
You are controlling a hospital phone-call appointment assistant.
Extract booking details from the caller's latest speech.
Do not diagnose. If symptoms sound urgent, set urgency to emergency.

Today is {date.today().isoformat()}.
Current call state:
{json.dumps(session, indent=2)}

Available doctors:
{doctor_context}

Caller said:
{message}

Return only valid JSON with these keys:
patient_name, patient_email, symptoms, requested_date, requested_time,
doctor_id, wants_booking, wants_change, urgency, reply
Use empty strings for unknown values.
"""

    response = openai_client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
    )
    return parse_ai_json(response.output_text)


def fallback_chat_reply(message, doctors, symptoms=""):
    combined_text = f"{symptoms} {message}".strip()
    recommendation = fallback_recommendation(combined_text, doctors)
    doctor = next(
        doctor for doctor in doctors
        if doctor["id"] == int(recommendation["recommended_doctor_id"])
    )
    return {
        "reply": (
            f"Based on what you shared, {doctor['name']} from "
            f"{doctor['specialty']} is a suitable choice. "
            "This is appointment guidance, not a diagnosis."
        ),
        "suggested_doctor_id": doctor["id"],
        "requested_date": "",
        "requested_time": "",
        "urgency": recommendation["urgency"],
        "next_action": "Select this doctor and choose an appointment date.",
        "should_book": False,
        "missing_fields": [],
    }


def fallback_voice_update(message, doctors, session):
    doctor = find_doctor_from_text(message, doctors)
    symptoms = session.get("symptoms") or message
    recommendation = fallback_recommendation(symptoms, doctors)
    return {
        "patient_name": "",
        "patient_email": "",
        "symptoms": symptoms,
        "requested_date": normalize_spoken_date(message),
        "requested_time": "",
        "doctor_id": str(doctor["id"] if doctor else recommendation["recommended_doctor_id"]),
        "wants_booking": "book" in message.lower() or "confirm" in message.lower(),
        "wants_change": "change" in message.lower(),
        "urgency": recommendation["urgency"],
        "reply": "",
    }


@app.before_request
def before_request():
    ensure_schema_updates()


def apply_voice_update(session, update, speech, doctors):
    for field in ("patient_name", "patient_email", "symptoms"):
        value = str(update.get(field, "")).strip()
        if value:
            session[field] = value

    if "skip email" in speech.lower() and not session.get("patient_email"):
        session["patient_email"] = make_placeholder_email(session.get("patient_phone", ""))

    requested_date = normalize_spoken_date(update.get("requested_date", "") or speech)
    if requested_date:
        session["appointment_date"] = requested_date

    doctor_id = str(update.get("doctor_id", "")).strip()
    if doctor_id.isdigit() and get_doctor(int(doctor_id)):
        session["doctor_id"] = doctor_id
    else:
        doctor = find_doctor_from_text(speech, doctors)
        if doctor:
            session["doctor_id"] = str(doctor["id"])

    if not session.get("doctor_id") and session.get("symptoms"):
        recommendation = fallback_recommendation(session["symptoms"], doctors)
        session["doctor_id"] = str(recommendation["recommended_doctor_id"])

    if session.get("doctor_id") and session.get("appointment_date"):
        slots = get_available_slots(
            int(session["doctor_id"]),
            session["appointment_date"],
        )
        requested_time = normalize_spoken_time(
            update.get("requested_time", "") or speech,
            slots,
        )
        if requested_time:
            session["appointment_time"] = requested_time


def book_voice_appointment(session):
    data = {
        "patient_name": session["patient_name"],
        "patient_email": session["patient_email"],
        "patient_phone": session["patient_phone"],
        "symptoms": session["symptoms"],
        "appointment_date": session["appointment_date"],
        "appointment_time": session["appointment_time"],
        "doctor_id": int(session["doctor_id"]),
    }

    available_slots = get_available_slots(data["doctor_id"], data["appointment_date"])
    if data["appointment_time"] not in available_slots:
        return None

    return execute_query(
        """
        INSERT INTO appointments
            (patient_name, patient_email, patient_phone, symptoms,
             appointment_date, appointment_time, doctor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            data["patient_name"],
            data["patient_email"],
            data["patient_phone"],
            data["symptoms"],
            data["appointment_date"],
            data["appointment_time"],
            data["doctor_id"],
        ),
    )


@app.route("/voice", methods=["GET", "POST"])
def voice():
    call_sid = request.values.get("CallSid", "local-call")
    caller_phone = request.values.get("From", "")
    get_voice_session(call_sid, caller_phone)
    return voice_gather(
        "Hello, you have reached the hospital appointment assistant. "
        "Please tell me your symptoms or which doctor you want to book."
    )


@app.route("/voice/respond", methods=["GET", "POST"])
def voice_respond():
    call_sid = request.values.get("CallSid", "local-call")
    caller_phone = request.values.get("From", "")
    speech = request.values.get("SpeechResult", "").strip()
    session = get_voice_session(call_sid, caller_phone)
    doctors = get_doctors()

    if not speech:
        return voice_gather("I did not catch that. Please say it again.")

    if "goodbye" in speech.lower() or "hang up" in speech.lower():
        call_sessions.pop(call_sid, None)
        return voice_say_and_hangup("Thank you for calling. Goodbye.")

    try:
        update = ai_voice_update(speech, doctors, session)
    except Exception:
        update = fallback_voice_update(speech, doctors, session)

    apply_voice_update(session, update, speech, doctors)

    if update.get("urgency") == "emergency":
        return voice_say_and_hangup(
            "Your symptoms may need urgent medical attention. "
            "Please call emergency services or go to the nearest emergency department now."
        )

    if "change" in speech.lower() or update.get("wants_change"):
        session["appointment_time"] = ""
        return voice_gather("Sure. Which date or time would you like instead?")

    if ("confirm" in speech.lower() or update.get("wants_booking")) and not missing_voice_fields(session):
        appointment_id = book_voice_appointment(session)
        if appointment_id:
            doctor = get_doctor(int(session["doctor_id"]))
            call_sessions.pop(call_sid, None)
            return voice_say_and_hangup(
                f"Done. Your appointment is booked with {doctor['name']} "
                f"on {session['appointment_date']} at {session['appointment_time']}. "
                f"Your appointment ID is {appointment_id}. Thank you."
            )
        session["appointment_time"] = ""
        return voice_gather("That slot is no longer available. Please choose another time.")

    missing = missing_voice_fields(session)
    if missing:
        next_field = missing[0]
        extra = ""
        if next_field == "appointment_time":
            extra = available_slot_text(session.get("doctor_id"), session.get("appointment_date"))
        return voice_gather(f"{update.get('reply') or ''} {extra} {FIELD_PROMPTS[next_field]}".strip())

    return voice_gather(build_booking_summary(session))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/doctors")
def doctors():
    return jsonify(get_doctors())


@app.route("/api/recommend", methods=["POST"])
def recommend():
    data = request.get_json(force=True)
    symptoms = data.get("symptoms", "").strip()

    if not symptoms:
        return jsonify({"error": "Please enter symptoms."}), 400

    doctors = get_doctors()

    try:
        recommendation = ai_recommendation(symptoms, doctors)
    except Exception:
        recommendation = fallback_recommendation(symptoms, doctors)

    selected = next(
        (
            doctor for doctor in doctors
            if doctor["id"] == int(recommendation.get("recommended_doctor_id", 0))
        ),
        doctors[0],
    )
    recommendation["recommended_doctor_id"] = selected["id"]
    recommendation["doctor"] = selected
    return jsonify(recommendation)


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    message = data.get("message", "").strip()
    symptoms = data.get("symptoms", "").strip()

    if not message:
        return jsonify({"error": "Please type a message."}), 400

    doctors = get_doctors()

    try:
        reply = ai_chat_reply(message, doctors, data)
    except Exception:
        reply = fallback_chat_reply(message, doctors, symptoms)

    return jsonify(reply)


@app.route("/api/availability")
def availability():
    doctor_id = request.args.get("doctor_id", type=int)
    appointment_date = request.args.get("date", "").strip()

    if not doctor_id or not appointment_date:
        return jsonify({"error": "Doctor and date are required."}), 400

    doctor = get_doctor(doctor_id)
    if not doctor:
        return jsonify({"error": "Doctor not found."}), 404

    slots = get_available_slots(doctor_id, appointment_date)
    return jsonify({
        "doctor": doctor,
        "date": appointment_date,
        "slots": slots,
    })


@app.route("/api/appointments", methods=["POST"])
def book_appointment():
    data = request.get_json(force=True)
    required = [
        "patient_name",
        "patient_email",
        "patient_phone",
        "symptoms",
        "appointment_date",
        "appointment_time",
        "doctor_id",
    ]

    missing = [field for field in required if not str(data.get(field, "")).strip()]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    available_slots = get_available_slots(
        int(data["doctor_id"]),
        data["appointment_date"],
    )
    if data["appointment_time"] not in available_slots:
        return jsonify({"error": "Selected time slot is no longer available."}), 409

    appointment_id = execute_query(
        """
        INSERT INTO appointments
            (patient_name, patient_email, patient_phone, symptoms,
             appointment_date, appointment_time, doctor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            data["patient_name"],
            data["patient_email"],
            data["patient_phone"],
            data["symptoms"],
            data["appointment_date"],
            data["appointment_time"],
            int(data["doctor_id"]),
        ),
    )

    return jsonify({
        "message": "Appointment booked successfully.",
        "appointment_id": appointment_id,
    }), 201


@app.route("/api/appointments")
def appointments():
    rows = fetch_all(
        """
        SELECT a.id, a.patient_name, a.patient_email, a.patient_phone,
               a.symptoms, a.appointment_date, a.appointment_time,
               a.status, a.created_at,
               d.name AS doctor_name, d.specialty
        FROM appointments a
        JOIN doctors d ON d.id = a.doctor_id
        ORDER BY a.created_at DESC
        """
    )
    return jsonify(rows)


if __name__ == "__main__":
    app.run(debug=True)
