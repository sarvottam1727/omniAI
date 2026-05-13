from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models import Contact, User
from app.schemas import ContactCreate, ContactRead, ImportResult
from app.services.contacts import import_contacts, parse_csv_bytes, parse_xlsx_bytes, upsert_contact

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("", response_model=ContactRead)
def create_contact(payload: ContactCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact, _ = upsert_contact(db, current_user.id, payload)
    return contact


@router.post("/import/csv", response_model=ImportResult)
async def import_csv(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        rows = parse_csv_bytes(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return import_contacts(db, current_user.id, rows, source="csv")


@router.post("/import/xlsx", response_model=ImportResult)
async def import_xlsx(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        rows = parse_xlsx_bytes(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return import_contacts(db, current_user.id, rows, source="xlsx")


@router.post("/import/api", response_model=ImportResult)
def import_api(rows: list[dict], current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return import_contacts(db, current_user.id, rows, source="api")


@router.get("", response_model=list[ContactRead])
def list_contacts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Contact).where(Contact.user_id == current_user.id).order_by(Contact.created_at.desc())).all()


@router.get("/{contact_id}", response_model=ContactRead)
def get_contact(contact_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.put("/{contact_id}", response_model=ContactRead)
def update_contact(contact_id: int, payload: ContactCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    for field, value in payload.model_dump().items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{contact_id}")
def delete_contact(contact_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    db.delete(contact)
    db.commit()
    return {"ok": True}
