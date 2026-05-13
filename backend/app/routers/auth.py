from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.deps import get_current_user
from app.models import OrganizationProfile, User
from app.schemas import LoginRequest, Token, UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=Token)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=payload.email.lower(), hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(OrganizationProfile(user_id=user.id))
    db.commit()
    return Token(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return Token(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)):
    return current_user
