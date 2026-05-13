from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models import Campaign, CampaignRecipient, CampaignStatus, User
from app.schemas import CampaignCreate, CampaignRead, CampaignUpdate, QueueResult, RecipientLogRead, ValidationResult
from app.services.audit import write_audit
from app.services.compliance import validate_campaign
from app.services.sending import queue_recipients
from app.worker import send_next_batch_task

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _owned_campaign(db: Session, campaign_id: int, user_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.user_id != user_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


@router.post("", response_model=CampaignRead)
def create_campaign(payload: CampaignCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = Campaign(user_id=current_user.id, **payload.model_dump())
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("", response_model=list[CampaignRead])
def list_campaigns(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Campaign).where(Campaign.user_id == current_user.id).order_by(Campaign.created_at.desc())).all()


@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _owned_campaign(db, campaign_id, current_user.id)


@router.put("/{campaign_id}", response_model=CampaignRead)
def update_campaign(campaign_id: int, payload: CampaignUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    if campaign.status not in {CampaignStatus.draft, CampaignStatus.paused}:
        raise HTTPException(status_code=409, detail="Campaign cannot be edited in current status")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/validate", response_model=ValidationResult)
def validate(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    result = validate_campaign(db, campaign)
    return ValidationResult(**{key: result[key] for key in ["eligible_count", "excluded_count", "checks", "exclusions", "can_send"]})


@router.post("/{campaign_id}/send-test")
def send_test(campaign_id: int, test_email: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    result = validate_campaign(db, campaign)
    hard_errors = [check for check in result["checks"] if not check["ok"] and check["severity"] == "error"]
    if hard_errors:
        raise HTTPException(status_code=400, detail={"message": "Fix compliance errors before sending tests", "checks": hard_errors})
    write_audit(db, user_id=current_user.id, campaign_id=campaign.id, action="send_test", metadata={"test_email": test_email})
    return {"queued": True, "message": "Test send accepted. Configure worker/provider to deliver."}


@router.post("/{campaign_id}/queue", response_model=QueueResult)
def queue(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    if campaign.status != CampaignStatus.draft:
        raise HTTPException(status_code=409, detail="Only draft campaigns can be queued")
    if not campaign.compliance_confirmed:
        raise HTTPException(status_code=400, detail="Compliance checklist must be confirmed")
    result = validate_campaign(db, campaign)
    if not result["can_send"]:
        raise HTTPException(status_code=400, detail=result)
    count = queue_recipients(db, campaign, result["eligible"])
    write_audit(
        db,
        user_id=current_user.id,
        campaign_id=campaign.id,
        action="campaign_queued",
        metadata={
            "recipient_count": count,
            "source_list": campaign.contact_list_id,
            "template_snapshot": campaign.email_template_html,
            "compliance_checks": result["checks"],
        },
    )
    try:
        send_next_batch_task.delay(campaign.id)
    except Exception:
        pass
    return QueueResult(campaign_id=campaign.id, queued_recipients=count, status=CampaignStatus.queued)


@router.post("/{campaign_id}/pause")
def pause(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    campaign.status = CampaignStatus.paused
    db.commit()
    return {"status": campaign.status}


@router.post("/{campaign_id}/resume")
def resume(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    if campaign.status != CampaignStatus.paused:
        raise HTTPException(status_code=409, detail="Only paused campaigns can be resumed")
    campaign.status = CampaignStatus.queued
    db.commit()
    send_next_batch_task.delay(campaign.id)
    return {"status": campaign.status}


@router.post("/{campaign_id}/cancel")
def cancel(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    campaign = _owned_campaign(db, campaign_id, current_user.id)
    campaign.status = CampaignStatus.cancelled
    db.commit()
    return {"status": campaign.status}


@router.get("/{campaign_id}/recipients", response_model=list[RecipientLogRead])
def recipient_logs(campaign_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owned_campaign(db, campaign_id, current_user.id)
    return db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)).all()
