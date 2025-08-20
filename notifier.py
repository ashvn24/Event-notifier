import requests
from twilio.rest import Client
from datetime import datetime
import pytz
import dotenv
import os

dotenv.load_dotenv()

tenant_id = os.getenv("TENANT_ID")
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
scope = os.getenv("SCOPE")

twilio_sid = os.getenv("TWILIO_SID")
twilio_token = os.getenv("TWILIO_TOKEN")
twilio_from = os.getenv("TWILIO_FROM")
twilio_to = os.getenv("TWILIO_TO")

ist = pytz.timezone('Asia/Kolkata')

# ---- Step 1: Get Access Token automatically ----
token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

token_data = {
    "grant_type": "client_credentials",
    "client_id": client_id,
    "client_secret": client_secret,
    "scope": scope
}

token_resp = requests.post(token_url, data=token_data)

access_token = token_resp.json().get("access_token")

# ---- Step 2: Query Today's Calendar Events ----
today = datetime.now().date()
start_time = today.isoformat() + "T00:00:00"
end_time = today.isoformat() + "T23:59:59"
calendar_url = "https://graph.microsoft.com/v1.0/users/akallan@hybridchart.com/calendar/events"

headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}
params = {
    "$filter": f"start/dateTime ge '{start_time}' and end/dateTime le '{end_time}'"
}

resp = requests.get(calendar_url, headers=headers, params=params)
events = resp.json().get("value", [])
event_list = []
for event in events:
    subject = event['subject']
    # Parse the UTC time string to datetime object
    start_utc = datetime.strptime(event['start']['dateTime'], '%Y-%m-%dT%H:%M:%S.%f0')
    # Localize to UTC
    start_utc = pytz.utc.localize(start_utc)
    # Convert to IST
    start_ist = start_utc.astimezone(ist)
    # Format to readable string
    start_str = start_ist.strftime('%Y-%m-%d %H:%M:%S %Z')
    
    status = event.get('responseStatus', {}).get('response', 'Unknown')
    event_list.append(f"{subject} at {start_str} - Status: {status}")

print(event_list)
if event_list:
    body = "Today's scheduled meetings:\n" + "\n".join(event_list)
else:
    body = "No meetings scheduled for today."

# ---- Step 3: Send WhatsApp Notification ----
client = Client(twilio_sid, twilio_token)
msg = client.messages.create(
    body=body,
    from_=twilio_from,
    to=twilio_to
)

print(f"WhatsApp notification sent: {msg.sid}")
