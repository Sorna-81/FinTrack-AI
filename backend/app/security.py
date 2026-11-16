import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import AuthSession, User

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
JWT_ALGORITHM = "HS256"


def hash_password(value: str) -> str:
    return pwd.hash(value)


def verify_password(value: str, hashed: str) -> bool:
    return pwd.verify(value, hashed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_access_token(user_id: int, session_id: str) -> str:
    now = _now()
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": session_id,
            "jti": secrets.token_urlsafe(18),
            "typ": "access",
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
        },
        settings.secret_key,
        algorithm=JWT_ALGORITHM,
    )


def _token_response(user_id: int, session_id: str, refresh_token: str) -> dict:
    access_token = make_access_token(user_id, session_id)
    return {
        "access_token": access_token,
        "token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "user": None,
    }


def create_session(db: Session, user: User) -> dict:
    session_id = uuid4().hex
    refresh_token = f"{session_id}.{secrets.token_urlsafe(48)}"
    now = _now()
    auth_session = AuthSession(
        id=session_id,
        user_id=user.id,
        refresh_token_hash=_hash_refresh(refresh_token),
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    db.add(auth_session)
    db.commit()
    response = _token_response(user.id, session_id, refresh_token)
    response["user"] = {"id": user.id, "name": user.name, "email": user.email}
    return response


def rotate_refresh_token(db: Session, refresh_token: str) -> dict:
    try:
        session_id, _secret = refresh_token.split(".", 1)
    except ValueError:
        raise HTTPException(401, "Invalid or expired refresh token")
    auth_session = db.query(AuthSession).filter_by(id=session_id).with_for_update().first()
    now = _now()
    if (
        not auth_session
        or auth_session.revoked_at is not None
        or _as_utc(auth_session.expires_at) <= now
    ):
        raise HTTPException(401, "Invalid or expired refresh token")
    if not hmac.compare_digest(auth_session.refresh_token_hash, _hash_refresh(refresh_token)):
        # A previously rotated token was replayed. Revoke the whole session.
        auth_session.revoked_at = now
        db.commit()
        raise HTTPException(401, "Refresh token reuse detected; sign in again")
    new_refresh_token = f"{session_id}.{secrets.token_urlsafe(48)}"
    auth_session.refresh_token_hash = _hash_refresh(new_refresh_token)
    auth_session.last_used_at = now
    db.commit()
    response = _token_response(auth_session.user_id, session_id, new_refresh_token)
    user = db.get(User, auth_session.user_id)
    response["user"] = {"id": user.id, "name": user.name, "email": user.email} if user else None
    return response


def revoke_refresh_token(db: Session, refresh_token: str) -> None:
    try:
        session_id, _secret = refresh_token.split(".", 1)
    except ValueError:
        return
    auth_session = db.query(AuthSession).filter_by(id=session_id).with_for_update().first()
    if auth_session and auth_session.revoked_at is None:
        now = _now()
        if hmac.compare_digest(auth_session.refresh_token_hash, _hash_refresh(refresh_token)):
            auth_session.revoked_at = now
            db.commit()


def current_user(token: str = Depends(oauth), db: Session = Depends(get_db)) -> User:
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
        if claims.get("typ") != "access":
            raise JWTError("Wrong token type")
        uid = int(claims.get("sub", ""))
        session_id = str(claims.get("sid", ""))
    except (JWTError, ValueError):
        raise HTTPException(401, "Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"})
    auth_session = db.query(AuthSession).filter_by(id=session_id, user_id=uid).first()
    if not auth_session or auth_session.revoked_at is not None or _as_utc(auth_session.expires_at) <= _now():
        raise HTTPException(401, "Session expired or revoked", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, uid)
    if not user:
        raise HTTPException(401, "User no longer exists", headers={"WWW-Authenticate": "Bearer"})
    return user
