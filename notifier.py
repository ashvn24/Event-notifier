from fastapi import FastAPI
from pydantic import BaseModel
import requests
from twilio.rest import Client
from datetime import datetime, date
import pytz
import dotenv
import os
from typing import List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

# Load env
dotenv.load_dotenv()

app = FastAPI()
logger = logging.getLogger("uvicorn.error")

# Pydantic model (kept for optional manual trigger)
class NotifyRequest(BaseModel):
    user_email: str

# Timezones
IST_TZ = pytz.timezone("Asia/Kolkata")
UTC_TZ = pytz.utc

# ---- Core function that can be used by both scheduler and endpoint ----
def notify_user_calendar(user_email: str) -> dict:
    tenant_id = os.getenv("TENANT_ID")
    client_id = os.getenv("CLIENT_ID")
    client_secret = os.getenv("CLIENT_SECRET")
    scope = os.getenv("SCOPE", "https://graph.microsoft.com/.default")

    twilio_sid = os.getenv("TWILIO_SID")
    twilio_token = os.getenv("TWILIO_TOKEN")
    twilio_from = os.getenv("TWILIO_FROM")  # e.g., "whatsapp:+14155238886"
    twilio_to = os.getenv("TWILIO_TO")      # e.g., "whatsapp:+91XXXXXXXXXX"

    # Validate required env vars
    required = {
        "TENANT_ID": tenant_id, "CLIENT_ID": client_id, "CLIENT_SECRET": client_secret,
        "TWILIO_SID": twilio_sid, "TWILIO_TOKEN": twilio_token,
        "TWILIO_FROM": twilio_from, "TWILIO_TO": twilio_to
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    # Step 1: Get Access Token
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    token_data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope
    }
    token_resp = requests.post(token_url, data=token_data, timeout=20)
    try:
        token_json = token_resp.json()
    except Exception:
        token_resp.raise_for_status()
        token_json = {}

    access_token = token_json.get("access_token")
    if not access_token:
        raise RuntimeError(f"Failed to get access token: {token_json}")

    # Step 2: Query Today's Calendar Events (today in IST, filter using UTC timestamps)
    # Compute today's 00:00:00 and 23:59:59 in IST, then convert to UTC
    today_ist = datetime.now(IST_TZ).date()
    start_ist = IST_TZ.localize(datetime.combine(today_ist, datetime.min.time()))
    end_ist = IST_TZ.localize(datetime.combine(today_ist, datetime.max.time().replace(microsecond=0)))
    start_utc = start_ist.astimezone(UTC_TZ).strftime("%Y-%m-%dT%H:%M:%S")
    end_utc = end_ist.astimezone(UTC_TZ).strftime("%Y-%m-%dT%H:%M:%S")

    calendar_url = f"https://graph.microsoft.com/v1.0/users/{user_email}/calendar/events"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    params = {
        "$filter": f"start/dateTime ge '{start_utc}' and end/dateTime le '{end_utc}'"
    }
    resp = requests.get(calendar_url, headers=headers, params=params, timeout=20)
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        data = {}

    if resp.status_code >= 400:
        raise RuntimeError(f"Graph API error {resp.status_code}: {data}")

    events = data.get("value", [])
    event_list: List[str] = []
    for event in events:
        subject = event.get("subject", "(No subject)")

        # Parse start time: Graph typically returns UTC or includes timeZone field
        start_raw = event.get("start", {}).get("dateTime")
        # Try multiple formats
        dt_obj = None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f0", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt_obj = datetime.strptime(start_raw, fmt)
                break
            except Exception:
                continue
        if dt_obj is None:
            # Fallback: try fromisoformat if available
            try:
                dt_obj = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                dt_obj = None

        # Assume UTC if no timezone, then convert to IST
        if dt_obj is not None:
            start_utc_dt = UTC_TZ.localize(dt_obj)
            start_ist_dt = start_utc_dt.astimezone(IST_TZ)
            start_str = start_ist_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        else:
            start_str = start_raw or "Unknown time"

        status = event.get("responseStatus", {}).get("response", "Unknown")
        event_list.append(f"{subject} at {start_str} - Status: {status}")

    if event_list:
        body = "Today's scheduled meetings:\n" + "\n".join(event_list)
    else:
        body = "No meetings scheduled for today."

    # Step 3: Send WhatsApp Notification
    client = Client(twilio_sid, twilio_token)
    msg = client.messages.create(
        body=body,
        from_=twilio_from,
        to=twilio_to
    )

    return {"message": "WhatsApp notification sent", "sid": msg.sid, "events": event_list}


# ---- Optional endpoint to trigger on-demand ----
@app.post("/notify")
def notify(request: NotifyRequest):
    result = notify_user_calendar(request.user_email)
    return result


# ---- Scheduler setup: run daily at 06:30 IST ----
scheduler = AsyncIOScheduler(timezone=IST_TZ)

def scheduled_job():
    # Choose a default email to monitor from env, or set here
    default_email = os.getenv("DEFAULT_USER_EMAIL")
    if not default_email:
        logger.error("DEFAULT_USER_EMAIL not set; skipping scheduled job.")
        return
    try:
        result = notify_user_calendar(default_email)
        logger.info(f"Scheduled notify sent: {result.get('sid')}, events={len(result.get('events', []))}")
    except Exception as e:
        logger.exception(f"Scheduled notify failed: {e}")

@app.on_event("startup")
def start_scheduler():
    # Run every day at 06:30 IST
    trigger = CronTrigger(hour=6, minute=30, timezone=IST_TZ)
    scheduler.add_job(scheduled_job, trigger, id="daily_notify_0630_ist", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started: daily at 06:30 IST")


@app.on_event("shutdown")
def shutdown_scheduler():
    scheduler.shutdown(wait=False)
