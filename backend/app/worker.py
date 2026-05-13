from celery import Celery

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.sending import send_next_batch

settings = get_settings()
celery_app = Celery("omniai_email_worker", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="campaigns.send_next_batch")
def send_next_batch_task(campaign_id: int) -> dict:
    db = SessionLocal()
    try:
        return send_next_batch(db, campaign_id)
    finally:
        db.close()
