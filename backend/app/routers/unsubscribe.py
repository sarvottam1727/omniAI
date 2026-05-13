from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import CampaignRecipient, ConsentStatus, Contact, EmailEvent, RecipientStatus, SuppressionEntry, SuppressionReason

router = APIRouter(prefix="/unsubscribe", tags=["unsubscribe"])


SUCCESS_HTML = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Unsubscribed</title></head>
<body style="font-family: system-ui; max-width: 680px; margin: 64px auto; line-height: 1.5;">
  <h1>You are unsubscribed</h1>
  <p>This address has been immediately suppressed from future marketing email.</p>
</body>
</html>
"""


@router.get("/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, db: Session = Depends(get_db)):
    recipient = db.scalar(select(CampaignRecipient).where(CampaignRecipient.unsubscribe_token == token))
    if not recipient:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe token")
    return SUCCESS_HTML


@router.post("/{token}")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    recipient = db.scalar(select(CampaignRecipient).where(CampaignRecipient.unsubscribe_token == token))
    if not recipient:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe token")
    contact = db.get(Contact, recipient.contact_id)
    contact.consent_status = ConsentStatus.unsubscribed
    recipient.status = RecipientStatus.unsubscribed
    exists = db.scalar(select(SuppressionEntry).where(SuppressionEntry.user_id == contact.user_id, SuppressionEntry.email == contact.email))
    if not exists:
        db.add(SuppressionEntry(user_id=contact.user_id, email=contact.email, reason=SuppressionReason.unsubscribed, source="unsubscribe"))
    db.add(EmailEvent(user_id=contact.user_id, campaign_id=recipient.campaign_id, contact_id=contact.id, event_type="unsubscribed"))
    db.commit()
    return {"ok": True, "message": "Contact unsubscribed and suppressed"}
