from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models import Contact, ContactList, ContactListMembership, User
from app.schemas import ContactListCreate, ContactListRead

router = APIRouter(prefix="/lists", tags=["lists"])


@router.post("", response_model=ContactListRead)
def create_list(payload: ContactListCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact_list = ContactList(user_id=current_user.id, **payload.model_dump())
    db.add(contact_list)
    db.commit()
    db.refresh(contact_list)
    return ContactListRead.model_validate(contact_list)


@router.get("", response_model=list[ContactListRead])
def lists(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ContactList).where(ContactList.user_id == current_user.id)).all()
    result = []
    for contact_list in rows:
        count = db.scalar(select(func.count(ContactListMembership.id)).where(ContactListMembership.contact_list_id == contact_list.id)) or 0
        item = ContactListRead.model_validate(contact_list)
        item.contact_count = count
        result.append(item)
    return result


@router.post("/{list_id}/contacts")
def add_contacts(list_id: int, contact_ids: list[int], current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact_list = db.get(ContactList, list_id)
    if not contact_list or contact_list.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="List not found")
    added = 0
    for contact_id in contact_ids:
        contact = db.get(Contact, contact_id)
        if not contact or contact.user_id != current_user.id:
            continue
        exists = db.scalar(
            select(ContactListMembership).where(
                ContactListMembership.contact_list_id == list_id,
                ContactListMembership.contact_id == contact_id,
            )
        )
        if not exists:
            db.add(ContactListMembership(contact_list_id=list_id, contact_id=contact_id))
            added += 1
    db.commit()
    return {"added": added}


@router.delete("/{list_id}/contacts/{contact_id}")
def remove_contact(list_id: int, contact_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    contact_list = db.get(ContactList, list_id)
    if not contact_list or contact_list.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="List not found")
    membership = db.scalar(
        select(ContactListMembership).where(
            ContactListMembership.contact_list_id == list_id,
            ContactListMembership.contact_id == contact_id,
        )
    )
    if membership:
        db.delete(membership)
        db.commit()
    return {"ok": True}
