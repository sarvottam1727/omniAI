from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, HttpUrl

from app.models import CampaignStatus, CampaignType, ConsentStatus, RecipientStatus, SuppressionReason


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    is_active: bool

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OrganizationProfileBase(BaseModel):
    company_name: str | None = None
    sender_name: str | None = None
    sender_email: EmailStr | None = None
    reply_to_email: EmailStr | None = None
    physical_address: str | None = None
    website_url: HttpUrl | None = None
    compliance_contact_email: EmailStr | None = None


class OrganizationProfileRead(OrganizationProfileBase):
    id: int
    user_id: int
    is_complete: bool = False

    model_config = {"from_attributes": True}


class ContactCreate(BaseModel):
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    phone: str | None = None
    source: str = "manual"
    consent_status: ConsentStatus = ConsentStatus.unknown
    consent_date: datetime | None = None
    consent_source: str | None = None
    tags: list[str] = []
    custom_fields: dict[str, Any] = {}


class ContactRead(ContactCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ImportResult(BaseModel):
    imported: int
    updated: int
    skipped: int
    errors: list[dict[str, Any]]


class ContactListCreate(BaseModel):
    name: str
    description: str | None = None


class ContactListRead(ContactListCreate):
    id: int
    created_at: datetime
    contact_count: int = 0

    model_config = {"from_attributes": True}


class CampaignCreate(BaseModel):
    campaign_name: str
    campaign_type: CampaignType
    subject: str
    preview_text: str | None = None
    purpose: str | None = None
    contact_list_id: int | None = None
    segment_filter: dict[str, Any] = {}
    email_template_html: str
    plain_text_version: str | None = None
    max_emails_per_minute: int = 60
    max_emails_per_hour: int = 1000
    max_emails_per_day: int = 5000
    provider_daily_limit: int = 5000


class CampaignUpdate(BaseModel):
    campaign_name: str | None = None
    campaign_type: CampaignType | None = None
    subject: str | None = None
    preview_text: str | None = None
    purpose: str | None = None
    contact_list_id: int | None = None
    segment_filter: dict[str, Any] | None = None
    email_template_html: str | None = None
    plain_text_version: str | None = None
    compliance_confirmed: bool | None = None
    max_emails_per_minute: int | None = None
    max_emails_per_hour: int | None = None
    max_emails_per_day: int | None = None
    provider_daily_limit: int | None = None


class CampaignRead(CampaignCreate):
    id: int
    status: CampaignStatus
    compliance_confirmed: bool
    created_at: datetime
    queued_at: datetime | None = None

    model_config = {"from_attributes": True}


class ValidationResult(BaseModel):
    eligible_count: int
    excluded_count: int
    checks: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    can_send: bool


class QueueResult(BaseModel):
    campaign_id: int
    queued_recipients: int
    status: CampaignStatus


class SuppressionCreate(BaseModel):
    email: EmailStr
    reason: SuppressionReason = SuppressionReason.manual
    source: str = "manual"


class SuppressionRead(SuppressionCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ProviderCredentialUpdate(BaseModel):
    provider: str = "mailpit"
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    encryption_type: str | None = None
    sender_email: EmailStr | None = None
    daily_limit: int = 500
    hourly_limit: int = 100


class ProviderCredentialRead(BaseModel):
    id: int
    provider: str
    smtp_host: str | None
    smtp_port: int | None
    smtp_username: str | None
    encryption_type: str | None
    sender_email: EmailStr | None
    daily_limit: int
    hourly_limit: int
    is_active: bool
    password_configured: bool


class RecipientLogRead(BaseModel):
    id: int
    email: EmailStr
    status: RecipientStatus
    failure_reason: str | None = None
    sent_at: datetime | None = None

    model_config = {"from_attributes": True}
