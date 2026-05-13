from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models import SuppressionEntry, User
from app.schemas import SuppressionCreate, SuppressionRead

router = APIRouter(prefix="/suppression", tags=["suppression"])


@router.get("", response_model=list[SuppressionRead])
def list_suppression(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(SuppressionEntry).where(SuppressionEntry.user_id == current_user.id)).all()


@router.post("", response_model=SuppressionRead)
def create_suppression(payload: SuppressionCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = db.scalar(select(SuppressionEntry).where(SuppressionEntry.user_id == current_user.id, SuppressionEntry.email == payload.email.lower()))
    if not entry:
        entry = SuppressionEntry(user_id=current_user.id, email=payload.email.lower(), reason=payload.reason, source=payload.source)
        db.add(entry)
        db.commit()
        db.refresh(entry)
    return entry


@router.delete("/{entry_id}")
def delete_suppression(entry_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    entry = db.get(SuppressionEntry, entry_id)
    if not entry or entry.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Suppression entry not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}
