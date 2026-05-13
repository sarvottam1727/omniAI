from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    *,
    user_id: int,
    action: str,
    campaign_id: int | None = None,
    metadata: dict | None = None,
) -> AuditLog:
    log = AuditLog(
        user_id=user_id,
        campaign_id=campaign_id,
        action=action,
        metadata_json=metadata or {},
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
