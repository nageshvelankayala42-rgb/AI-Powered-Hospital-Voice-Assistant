# AI Hospital Appointment Booking System

Flask + MySQL appointment booking system with an OpenAI-powered assistant for doctor recommendations.

## Features

- Patient appointment form
- Doctor list from MySQL
- AI recommendation based on symptoms
- Local fallback recommendation when OpenAI is unavailable
- Appointment storage in MySQL
- Simple responsive HTML, CSS, and JavaScript frontend

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

## Notes

The OpenAI integration uses the Responses API through the official Python SDK:

```python
response = openai_client.responses.create(
    model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    input=prompt,
)
```

Set `OPENAI_API_KEY` in `.env` before using the AI recommendation feature.
