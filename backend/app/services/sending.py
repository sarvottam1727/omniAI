from datetime import datetime, timezone
from jinja2 import Template
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    EmailEvent,
    OrganizationProfile,
    ProviderCredential,
    RecipientStatus,
)
from app.services.providers import provider_from_config


def render_for_contact(campaign: Campaign, contact: Contact, profile: OrganizationProfile, unsubscribe_url: str) -> tuple[str, str | None]:
    variables = {
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "company": contact.company or "",
        "unsubscribe_url": unsubscribe_url,
        "sender_name": profile.sender_name or "",
        "physical_address": profile.physical_address or "",
    }
    html = Template(campaign.email_template_html).render(**variables)
    text = Template(campaign.plain_text_version or "").render(**variables) if campaign.plain_text_version else None
    return html, text


def queue_recipients(db: Session, campaign: Campaign, eligible_contacts: list[Contact]) -> int:
    count = 0
    for contact in eligible_contacts:
        exists = db.scalar(
            select(CampaignRecipient).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.contact_id == contact.id,
            )
        )
        if exists:
            continue
        db.add(CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id, email=contact.email))
        count += 1
    campaign.status = CampaignStatus.queued
    campaign.queued_at = datetime.now(timezone.utc)
    db.commit()
    return count


def campaign_sent_today(db: Session, campaign: Campaign) -> int:
    return db.scalar(
        select(func.count(CampaignRecipient.id)).where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status.in_([RecipientStatus.sent, RecipientStatus.delivered]),
        )
    ) or 0


def send_next_batch(db: Session, campaign_id: int) -> dict:
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.status not in {CampaignStatus.queued, CampaignStatus.sending}:
        return {"sent": 0, "failed": 0, "paused": False}

    if campaign_sent_today(db, campaign) >= campaign.provider_daily_limit:
        campaign.status = CampaignStatus.paused
        db.commit()
        return {"sent": 0, "failed": 0, "paused": True, "reason": "provider daily limit reached"}

    profile = db.scalar(select(OrganizationProfile).where(OrganizationProfile.user_id == campaign.user_id))
    credential = db.scalar(
        select(ProviderCredential).where(ProviderCredential.user_id == campaign.user_id, ProviderCredential.is_active.is_(True))
    )
    settings = get_settings()
    provider_name = credential.provider if credential else "mailpit"
    provider = provider_from_config(
        provider_name,
        host=credential.smtp_host if credential else settings.local_mail_host,
        port=credential.smtp_port if credential else settings.local_mail_port,
        username=credential.smtp_username if credential else None,
        password=decrypt_secret(credential.encrypted_smtp_password) if credential else None,
        encryption_type=credential.encryption_type if credential else None,
    )

    campaign.status = CampaignStatus.sending
    recipients = db.scalars(
        select(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status == RecipientStatus.queued)
        .limit(campaign.max_emails_per_minute)
    ).all()

    sent = failed = 0
    for recipient in recipients:
        contact = db.get(Contact, recipient.contact_id)
        unsubscribe_url = f"{settings.public_base_url}/unsubscribe/{recipient.unsubscribe_token}"
        html, text = render_for_contact(campaign, contact, profile, unsubscribe_url)
        recipient.status = RecipientStatus.sending
        db.commit()
        result = provider.send(
            to_email=recipient.email,
            from_email=profile.sender_email,
            reply_to=profile.reply_to_email,
            subject=campaign.subject,
            html=html,
            text=text,
        )
        if result.success:
            recipient.status = RecipientStatus.sent
            recipient.sent_at = datetime.now(timezone.utc)
            sent += 1
            event_type = "sent"
        else:
            recipient.status = RecipientStatus.failed if result.permanent_failure else RecipientStatus.queued
            recipient.failure_reason = result.reason
            failed += 1
            event_type = "failed" if result.permanent_failure else "retry_scheduled"
        db.add(
            EmailEvent(
                user_id=campaign.user_id,
                campaign_id=campaign.id,
                contact_id=recipient.contact_id,
                event_type=event_type,
                provider_message_id=result.provider_message_id,
                metadata_json={"reason": result.reason},
            )
        )
        db.commit()

    remaining = db.scalar(
        select(func.count(CampaignRecipient.id)).where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status == RecipientStatus.queued,
        )
    )
    if remaining == 0:
        campaign.status = CampaignStatus.sent
        db.commit()

    return {"sent": sent, "failed": failed, "paused": False}
