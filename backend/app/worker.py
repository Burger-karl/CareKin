"""Celery app for background and scheduled jobs (PRD 10.1).

Run a worker and the scheduler (from backend/):
    celery -A app.worker worker --loglevel=info
    celery -A app.worker beat --loglevel=info

Crisis alerts are first sent from the API process itself, so they don't depend on
this worker. Beat runs crisis.sweep_alerts every 30 seconds to retry failures.
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "carekin",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.domains.crisis.tasks"],
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # a task lost with its worker is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=120,
    task_soft_time_limit=90,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    beat_schedule={
        "crisis-sweep-alerts": {
            "task": "crisis.sweep_alerts",
            "schedule": 30.0,
            "options": {"expires": 25},  # never let sweeps pile up
        },
    },
)
