from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import analytics, auth, campaigns, contacts, lists, organization, settings, suppression, unsubscribe, webhooks

app = FastAPI(
    title="OmniAI Email Shooter API",
    description="Consent-based bulk email campaign system with compliance guardrails.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(organization.router)
app.include_router(contacts.router)
app.include_router(lists.router)
app.include_router(campaigns.router)
app.include_router(suppression.router)
app.include_router(webhooks.router)
app.include_router(unsubscribe.router)
app.include_router(settings.router)
app.include_router(analytics.router)


@app.get("/health")
def health():
    return {"ok": True, "service": "omniai-email-shooter"}
