# AI-Powered Hospital Voice Assistant

Flask + MySQL appointment booking system with an OpenAI-powered browser assistant and Twilio phone-call assistant.

## Features

- Patient appointment form
- Doctor list from MySQL
- AI recommendation based on symptoms
- Browser voice assistant with speech input and spoken replies
- Real phone-call assistant using Twilio Voice webhooks
- Phone callers can speak symptoms, check availability, and confirm booking
- Local fallback recommendation when OpenAI is unavailable
- Appointment storage in MySQL
- Doctor availability and 30-minute appointment slots

## Setup

1. Create a virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create the database:

```bash
mysql -u root -p < schema.sql
```

4. Create `.env` from `.env.example` and add your values:

```bash
copy .env.example .env
```

5. Run the app:

```bash
python app.py
```

Open `http://127.0.0.1:5000`.

## Real Phone-Call Assistant Setup

This project uses Twilio Programmable Voice for real phone calls.

1. Install the latest dependencies:

```bash
pip install -r requirements.txt
```

2. Run Flask:

```bash
python app.py
```

3. Expose your local Flask app with ngrok:

```bash
ngrok http 5000
```

4. Copy the HTTPS forwarding URL from ngrok, for example:

```text
https://abc123.ngrok-free.app
```

5. In Twilio Console, open your phone number and set:

```text
A call comes in: Webhook
URL: https://abc123.ngrok-free.app/voice
Method: POST
```

6. Call your Twilio number and speak naturally:

```text
I have headache and dizziness. Please book an appointment tomorrow.
```

The assistant will collect missing details, check available doctor slots, and ask for confirmation before saving the appointment.

## Phone Call Endpoints

- `/voice` answers the incoming call.
- `/voice/respond` receives speech text from Twilio and continues the call.
- `/api/availability` returns open slots for a doctor and date.

## Notes

The OpenAI integration uses the Responses API through the official Python SDK:

```python
response = openai_client.responses.create(
    model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    input=prompt,
)
```

Set `OPENAI_API_KEY` in `.env` before using the AI recommendation feature.

Do not push `.env` to GitHub. It contains private API keys and database passwords.
