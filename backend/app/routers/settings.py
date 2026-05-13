from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.db.session import get_db
from app.deps import get_current_user
from app.models import ProviderCredential, User
from app.schemas import ProviderCredentialRead, ProviderCredentialUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


def _mask(credential: ProviderCredential) -> ProviderCredentialRead:
    return ProviderCredentialRead(
        id=credential.id,
        provider=credential.provider,
        smtp_host=credential.smtp_host,
        smtp_port=credential.smtp_port,
        smtp_username=credential.smtp_username,
        encryption_type=credential.encryption_type,
        sender_email=credential.sender_email,
        daily_limit=credential.daily_limit,
        hourly_limit=credential.hourly_limit,
        is_active=credential.is_active,
        password_configured=bool(credential.encrypted_smtp_password),
    )


@router.get("/provider", response_model=ProviderCredentialRead | None)
def get_provider(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    credential = db.scalar(select(ProviderCredential).where(ProviderCredential.user_id == current_user.id, ProviderCredential.is_active.is_(True)))
    return _mask(credential) if credential else None


@router.put("/provider", response_model=ProviderCredentialRead)
def update_provider(payload: ProviderCredentialUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    credential = db.scalar(select(ProviderCredential).where(ProviderCredential.user_id == current_user.id, ProviderCredential.is_active.is_(True)))
    if not credential:
        credential = ProviderCredential(user_id=current_user.id)
        db.add(credential)
    data = payload.model_dump()
    password = data.pop("smtp_password")
    for field, value in data.items():
        setattr(credential, field, value)
    if password:
        credential.encrypted_smtp_password = encrypt_secret(password)
    db.commit()
    db.refresh(credential)
    return _mask(credential)
