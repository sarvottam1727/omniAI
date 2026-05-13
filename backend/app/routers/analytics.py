from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models import Campaign, CampaignRecipient, RecipientStatus, User

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard")
def dashboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaigns = db.scalars(select(Campaign).where(Campaign.user_id == current_user.id)).all()
    campaign_ids = [campaign.id for campaign in campaigns]
    counts = {status.value: 0 for status in RecipientStatus}
    total_recipients = 0
    if campaign_ids:
        rows = db.execute(
            select(CampaignRecipient.status, func.count(CampaignRecipient.id))
            .where(CampaignRecipient.campaign_id.in_(campaign_ids))
            .group_by(CampaignRecipient.status)
        ).all()
        for status, count in rows:
            counts[status.value] = count
            total_recipients += count
    delivered = counts["delivered"] + counts["sent"]
    delivery_rate = round((delivered / total_recipients) * 100, 2) if total_recipients else 0
    return {
        "campaigns_sent": len([campaign for campaign in campaigns if campaign.status.value == "sent"]),
        "total_campaigns": len(campaigns),
        "total_recipients": total_recipients,
        "counts": counts,
        "delivery_rate": delivery_rate,
        "tracking_enabled": False,
    }
