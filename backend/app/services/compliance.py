import re
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Campaign,
    CampaignType,
    ConsentStatus,
    Contact,
    ContactListMembership,
    OrganizationProfile,
    SuppressionEntry,
)
from app.services.contacts import is_valid_email

MARKETING_TYPES = {
    CampaignType.marketing,
    CampaignType.newsletter,
    CampaignType.sales_outreach,
    CampaignType.job_outreach,
    CampaignType.follow_up,
}

REQUIRED_PROFILE_FIELDS = [
    "company_name",
    "sender_name",
    "sender_email",
    "reply_to_email",
    "physical_address",
    "website_url",
    "compliance_contact_email",
]


def profile_is_complete(profile: OrganizationProfile | None) -> bool:
    return bool(profile and all(getattr(profile, field) for field in REQUIRED_PROFILE_FIELDS))


def _contacts_for_campaign(db: Session, campaign: Campaign) -> list[Contact]:
    query = select(Contact).where(Contact.user_id == campaign.user_id)
    if campaign.contact_list_id:
        query = query.join(ContactListMembership).where(
            ContactListMembership.contact_list_id == campaign.contact_list_id
        )
    filters = campaign.segment_filter or {}
    if tags := filters.get("tags"):
        query = query.where(Contact.tags.contains(tags))
    if source := filters.get("source"):
        query = query.where(Contact.source == source)
    if company := filters.get("company"):
        query = query.where(Contact.company == company)
    if consent := filters.get("consent_status"):
        query = query.where(Contact.consent_status == ConsentStatus(consent))
    return list(db.scalars(query).all())


def spam_risk_checks(campaign: Campaign, profile: OrganizationProfile | None, recipient_count: int) -> list[dict]:
    html = campaign.email_template_html or ""
    checks = [
        {
            "key": "unsubscribe_link",
            "ok": "{{unsubscribe_url}}" in html or "unsubscribe" in html.lower(),
            "severity": "error" if campaign.campaign_type in MARKETING_TYPES else "warning",
            "message": "Marketing-style campaigns require a one-click unsubscribe link.",
        },
        {
            "key": "physical_address",
            "ok": bool(profile and profile.physical_address and profile.physical_address in html),
            "severity": "error",
            "message": "A physical address must be present in the email footer.",
        },
        {
            "key": "plain_text",
            "ok": bool(campaign.plain_text_version),
            "severity": "warning",
            "message": "Plain text fallback is recommended for accessibility and deliverability.",
        },
        {
            "key": "too_many_links",
            "ok": len(re.findall(r"https?://", html)) <= 12,
            "severity": "warning",
            "message": "Too many links can look risky to recipients and providers.",
        },
        {
            "key": "deceptive_subject",
            "ok": not any(term in campaign.subject.lower() for term in ["re:", "fwd:", "urgent!!!", "free money"]),
            "severity": "warning",
            "message": "Subject should be truthful and not simulate an existing thread.",
        },
        {
            "key": "too_many_recipients",
            "ok": recipient_count <= campaign.provider_daily_limit,
            "severity": "error",
            "message": "Recipient count exceeds configured provider daily limit.",
        },
        {
            "key": "purpose",
            "ok": bool(campaign.purpose),
            "severity": "error",
            "message": "Campaign purpose is required before sending.",
        },
    ]
    return checks


def validate_campaign(db: Session, campaign: Campaign) -> dict:
    profile = db.scalar(select(OrganizationProfile).where(OrganizationProfile.user_id == campaign.user_id))
    contacts = _contacts_for_campaign(db, campaign)
    suppressions = {
        entry.email: entry.reason.value
        for entry in db.scalars(select(SuppressionEntry).where(SuppressionEntry.user_id == campaign.user_id)).all()
    }

    eligible: list[Contact] = []
    exclusions: list[dict] = []
    seen: set[str] = set()

    for contact in contacts:
        reason = None
        if contact.email in seen:
            reason = "duplicate"
        elif not is_valid_email(contact.email):
            reason = "invalid email"
        elif contact.email in suppressions:
            reason = f"suppression list: {suppressions[contact.email]}"
        elif contact.consent_status == ConsentStatus.unsubscribed:
            reason = "unsubscribed"
        elif contact.consent_status == ConsentStatus.bounced:
            reason = "bounced"
        elif contact.consent_status == ConsentStatus.complained:
            reason = "complained"
        elif campaign.campaign_type in MARKETING_TYPES and contact.consent_status == ConsentStatus.unknown:
            reason = "no consent"
        elif campaign.campaign_type == CampaignType.transactional and contact.consent_status == ConsentStatus.unknown:
            reason = None

        seen.add(contact.email)
        if reason:
            exclusions.append({"contact_id": contact.id, "email": contact.email, "reason": reason})
        else:
            eligible.append(contact)

    checks = [
        {
            "key": "sender_profile",
            "ok": profile_is_complete(profile),
            "severity": "error",
            "message": "Complete sender profile is required before sending.",
        }
    ]
    checks.extend(spam_risk_checks(campaign, profile, len(eligible)))
    blocking_errors = [check for check in checks if not check["ok"] and check["severity"] == "error"]

    return {
        "eligible": eligible,
        "eligible_count": len(eligible),
        "excluded_count": len(exclusions),
        "checks": checks,
        "exclusions": exclusions,
        "can_send": not blocking_errors and len(eligible) > 0,
    }
