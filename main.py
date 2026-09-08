from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import stripe
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials, firestore
import os
from dotenv import load_dotenv

# -------------------------------------------------
# LOAD ENV + FIREBASE + STRIPE
# -------------------------------------------------
load_dotenv()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL")  # your Streamlit URL, e.g. https://warehouse-productivity-app.streamlit.app
PRICE_ID = os.getenv("STRIPE_PRICE_ID")   # your Stripe subscription price ID

stripe.api_key = STRIPE_SECRET_KEY

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or [FRONTEND_URL]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# REQUEST MODELS
# -------------------------------------------------
class CheckoutRequest(BaseModel):
    id_token: str


# -------------------------------------------------
# HELPER: GET USER DOC FROM FIRESTORE
# -------------------------------------------------
def get_user_doc(uid: str):
    return db.collection("users").document(uid)


# -------------------------------------------------
# /check_license
# -------------------------------------------------
@app.post("/check_license")
async def check_license(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    id_token = auth_header.split("Bearer ")[1]

    try:
        decoded = firebase_auth.verify_id_token(id_token)
        uid = decoded["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Firebase ID token")

    doc_ref = get_user_doc(uid)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=403, detail="No subscription record found")

    data = doc.to_dict()
    status = data.get("subscription_status", "inactive")

    if status != "active":
        raise HTTPException(status_code=403, detail="Subscription not active")

    return {"status": "ok", "message": "License valid"}


# -------------------------------------------------
# /create_checkout_session
# -------------------------------------------------
@app.post("/create_checkout_session")
async def create_checkout_session(body: CheckoutRequest):
    # verify Firebase token
    try:
        decoded = firebase_auth.verify_id_token(body.id_token)
        uid = decoded["uid"]
        email = decoded.get("email")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Firebase ID token")

    # create or reuse Stripe customer
    doc_ref = get_user_doc(uid)
    doc = doc_ref.get()

    customer_id = None
    if doc.exists:
        data = doc.to_dict()
        customer_id = data.get("stripe_customer_id")

    if not customer_id:
        customer = stripe.Customer.create(email=email)
        customer_id = customer["id"]
        doc_ref.set(
            {
                "stripe_customer_id": customer_id,
                "subscription_status": "inactive",
            },
            merge=True,
        )

    # create checkout session
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": PRICE_ID, "quantity": 1}],
            success_url=f"{FRONTEND_URL}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}?canceled=true",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")

    return {"checkout_url": session.url}

