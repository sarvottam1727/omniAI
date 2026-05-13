from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models import OrganizationProfile, User
from app.schemas import OrganizationProfileBase, OrganizationProfileRead
from app.services.compliance import profile_is_complete

router = APIRouter(prefix="/organization", tags=["organization"])


def _read(profile: OrganizationProfile) -> OrganizationProfileRead:
    data = OrganizationProfileRead.model_validate(profile)
    data.is_complete = profile_is_complete(profile)
    return data


@router.get("/profile", response_model=OrganizationProfileRead)
def get_profile(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(OrganizationProfile).where(OrganizationProfile.user_id == current_user.id))
    if not profile:
        profile = OrganizationProfile(user_id=current_user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return _read(profile)


@router.put("/profile", response_model=OrganizationProfileRead)
def update_profile(
    payload: OrganizationProfileBase,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.scalar(select(OrganizationProfile).where(OrganizationProfile.user_id == current_user.id))
    if not profile:
        profile = OrganizationProfile(user_id=current_user.id)
        db.add(profile)
    for field, value in payload.model_dump().items():
        setattr(profile, field, str(value) if value is not None else None)
    db.commit()
    db.refresh(profile)
    return _read(profile)
