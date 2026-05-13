from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import CampaignRecipient, Contact, EmailEvent, RecipientStatus, SuppressionEntry, SuppressionReason

router = APIRouter(prefix="/webhooks/email", tags=["webhooks"])


def _apply_event(db: Session, token: str | None, email: str | None, event_type: str, suppress_reason: SuppressionReason | None = None):
    recipient = None
    if token:
        recipient = db.scalar(select(CampaignRecipient).where(CampaignRecipient.unsubscribe_token == token))
    if not recipient and email:
        recipient = db.scalar(select(CampaignRecipient).where(CampaignRecipient.email == email.lower()).order_by(CampaignRecipient.id.desc()))
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient event target not found")
    contact = db.get(Contact, recipient.contact_id)
    if event_type == "delivery":
        recipient.status = RecipientStatus.delivered
    elif event_type == "bounce":
        recipient.status = RecipientStatus.bounced
    elif event_type == "complaint":
        recipient.status = RecipientStatus.complained
    if suppress_reason:
        contact.consent_status = suppress_reason.value
        exists = db.scalar(select(SuppressionEntry).where(SuppressionEntry.user_id == contact.user_id, SuppressionEntry.email == recipient.email))
        if not exists:
            db.add(SuppressionEntry(user_id=contact.user_id, email=recipient.email, reason=suppress_reason, source="webhook"))
    db.add(EmailEvent(user_id=contact.user_id, campaign_id=recipient.campaign_id, contact_id=contact.id, event_type=event_type))
    db.commit()
    return {"ok": True}


@router.post("/bounce")
def bounce(payload: dict, db: Session = Depends(get_db)):
    return _apply_event(db, payload.get("token"), payload.get("email"), "bounce", SuppressionReason.bounced)


@router.post("/complaint")
def complaint(payload: dict, db: Session = Depends(get_db)):
    return _apply_event(db, payload.get("token"), payload.get("email"), "complaint", SuppressionReason.complained)


@router.post("/delivery")
def delivery(payload: dict, db: Session = Depends(get_db)):
    return _apply_event(db, payload.get("token"), payload.get("email"), "delivery")
