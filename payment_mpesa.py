"""
M-Pesa Payment Integration (Standalone)
---------------------------------------
This module handles:
- Generating M-Pesa STK push requests (Lipa na M-Pesa)
- Verifying payment callbacks
- Granting 30-day access upon successful payment

Later, you’ll integrate this into your Telegram bot.
"""

import base64
import json
import time
import requests
from datetime import datetime

# ------------------ CONFIGURATION ------------------
# Replace with your own Daraja credentials (from https://developer.safaricom.co.ke/)
CONSUMER_KEY = "GhZbGAn1OVMgqRkuq4FpIAQawyBf5VykKg3AOFYIKFDodblG"
CONSUMER_SECRET = "SBPremzofrVQUBFFTnnQGsF2XJUpsPXdUP15KdQwXyIIim7D4IRF5Lb7uEacfBCj"
BUSINESS_SHORTCODE = "174379"       # use your paybill or till number
PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
CALLBACK_URL = "https://yourdomain.com/mpesa/callback"  # you can use Ngrok for testing

# ------------------ TOKEN GENERATION ------------------
def get_access_token():
    """
    Obtain OAuth token from Safaricom API.
    """
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    response = requests.get(url, auth=(CONSUMER_KEY, CONSUMER_SECRET))
    access_token = json.loads(response.text)["access_token"]
    return access_token

# ------------------ STK PUSH REQUEST ------------------
def lipa_na_mpesa(phone_number, amount):
    """
    Send STK Push to user's phone to authorize payment.
    - phone_number: 2547XXXXXXXX
    - amount: integer amount in KES
    """
    access_token = get_access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    password = base64.b64encode((BUSINESS_SHORTCODE + PASSKEY + timestamp).encode()).decode()

    payload = {
        "BusinessShortCode": BUSINESS_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone_number,
        "PartyB": BUSINESS_SHORTCODE,
        "PhoneNumber": phone_number,
        "CallBackURL": CALLBACK_URL,
        "AccountReference": "SageBot Subscription",
        "TransactionDesc": "SageBot 30-day Access Payment",
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
    response = requests.post(url, json=payload, headers=headers)

    try:
        data = response.json()
    except:
        data = {"error": response.text}

    return data

# ------------------ CALLBACK SIMULATION ------------------
def simulate_callback(data):
    """
    Simulate what happens when Safaricom calls your callback endpoint.
    In production, you’ll receive this automatically via Flask/FastAPI webhook.
    """
    result_code = data.get("Body", {}).get("stkCallback", {}).get("ResultCode")
    phone_number = data.get("Body", {}).get("stkCallback", {}).get("CallbackMetadata", {}).get("Item", [])[4].get("Value", None)

    if result_code == 0:
        # Payment successful
        print(f"✅ Payment confirmed for {phone_number}")
        # Here you’ll grant 30-day access to this phone_number’s Telegram account
        # e.g., call: grant_user(user_id, 30)
        return True
    else:
        print(f"❌ Payment failed for {phone_number}")
        return False

# ------------------ TEST RUN ------------------
if __name__ == "__main__":
    print("Simulating M-Pesa STK push...")
    phone = input("Enter phone number (2547XXXXXXXX): ")
    response = lipa_na_mpesa(phone, amount=800)
    print(json.dumps(response, indent=2))