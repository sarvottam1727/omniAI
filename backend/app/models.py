import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ConsentStatus(str, enum.Enum):
    opted_in = "opted_in"
    soft_opt_in = "soft_opt_in"
    transactional = "transactional"
    unknown = "unknown"
    unsubscribed = "unsubscribed"
    bounced = "bounced"
    complained = "complained"


class CampaignType(str, enum.Enum):
    marketing = "marketing"
    transactional = "transactional"
    newsletter = "newsletter"
    follow_up = "follow_up"
    job_outreach = "job_outreach"
    sales_outreach = "sales_outreach"


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    sending = "sending"
    paused = "paused"
    sent = "sent"
    cancelled = "cancelled"


class RecipientStatus(str, enum.Enum):
    queued = "queued"
    sending = "sending"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"
    bounced = "bounced"
    unsubscribed = "unsubscribed"
    complained = "complained"
    skipped = "skipped"


class SuppressionReason(str, enum.Enum):
    unsubscribed = "unsubscribed"
    bounced = "bounced"
    complained = "complained"
    manual = "manual"
    provider_blocked = "provider_blocked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    organization: Mapped["OrganizationProfile"] = relationship(back_populates="user", uselist=False)


class OrganizationProfile(Base):
    __tablename__ = "organization_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    sender_email: Mapped[str | None] = mapped_column(String(320))
    reply_to_email: Mapped[str | None] = mapped_column(String(320))
    physical_address: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(500))
    compliance_contact_email: Mapped[str | None] = mapped_column(String(320))

    user: Mapped[User] = relationship(back_populates="organization")


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_contact_user_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    company: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(120), default="manual")
    consent_status: Mapped[ConsentStatus] = mapped_column(Enum(ConsentStatus), default=ConsentStatus.unknown)
    consent_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_source: Mapped[str | None] = mapped_column(String(255))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    memberships: Mapped[list["ContactListMembership"]] = relationship(back_populates="contact")


class ContactList(Base):
    __tablename__ = "contact_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    memberships: Mapped[list["ContactListMembership"]] = relationship(back_populates="contact_list")


class ContactListMembership(Base):
    __tablename__ = "contact_list_memberships"
    __table_args__ = (UniqueConstraint("contact_list_id", "contact_id", name="uq_list_contact"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_list_id: Mapped[int] = mapped_column(ForeignKey("contact_lists.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    contact_list: Mapped[ContactList] = relationship(back_populates="memberships")
    contact: Mapped[Contact] = relationship(back_populates="memberships")


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    html: Mapped[str] = mapped_column(Text)
    plain_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    campaign_name: Mapped[str] = mapped_column(String(255))
    campaign_type: Mapped[CampaignType] = mapped_column(Enum(CampaignType))
    subject: Mapped[str] = mapped_column(String(255))
    preview_text: Mapped[str | None] = mapped_column(String(255))
    purpose: Mapped[str | None] = mapped_column(Text)
    contact_list_id: Mapped[int | None] = mapped_column(ForeignKey("contact_lists.id"))
    segment_filter: Mapped[dict] = mapped_column(JSON, default=dict)
    email_template_html: Mapped[str] = mapped_column(Text)
    plain_text_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus), default=CampaignStatus.draft)
    compliance_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    max_emails_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    max_emails_per_hour: Mapped[int] = mapped_column(Integer, default=1000)
    max_emails_per_day: Mapped[int] = mapped_column(Integer, default=5000)
    provider_daily_limit: Mapped[int] = mapped_column(Integer, default=5000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipients: Mapped[list["CampaignRecipient"]] = relationship(back_populates="campaign")


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_contact"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    status: Mapped[RecipientStatus] = mapped_column(Enum(RecipientStatus), default=RecipientStatus.queued)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    unsubscribe_token: Mapped[str] = mapped_column(String(64), default=lambda: uuid.uuid4().hex, unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")


class SuppressionEntry(Base):
    __tablename__ = "suppression_entries"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_suppression_user_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    reason: Mapped[SuppressionReason] = mapped_column(Enum(SuppressionReason))
    source: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="mailpit")
    smtp_host: Mapped[str | None] = mapped_column(String(255))
    smtp_port: Mapped[int | None] = mapped_column(Integer)
    smtp_username: Mapped[str | None] = mapped_column(String(255))
    encrypted_smtp_password: Mapped[str | None] = mapped_column(Text)
    encryption_type: Mapped[str | None] = mapped_column(String(30))
    sender_email: Mapped[str | None] = mapped_column(String(320))
    daily_limit: Mapped[int] = mapped_column(Integer, default=500)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    action: Mapped[str] = mapped_column(String(120), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
