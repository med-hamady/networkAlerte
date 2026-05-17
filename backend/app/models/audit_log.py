"""
Audit log of state-changing HTTP requests.

One append-only row per mutation (POST/PUT/PATCH/DELETE) recorded by the
FastAPI middleware in app.main. Read-only after creation — Base.updated_at is
inherited but never touched.

Forensic intent: answer "who/what/when" after the fact, even when applicative
logs have rotated. The companion detection job
(tasks.jobs.security_anomaly_detection_job) counts rows per client IP over a
sliding window and notifies operators when a threshold is exceeded.
"""

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    """One row per state-changing HTTP request."""

    __tablename__ = "audit_log"

    # HTTP verb (POST/PUT/PATCH/DELETE). Joint index with created_at powers
    # the detection job's "count mutations in last N minutes per IP" query.
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    # Request path, capped at 500 chars — long enough for any /api/v1/... URL.
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Best-effort client IP — read from X-Real-IP / X-Forwarded-For (nginx) or
    # request.client.host. 45 chars covers IPv6.
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # HTTP status returned to the client (informs forensic triage).
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # Truncated User-Agent — useful for IoC identification, capped to keep rows light.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_client_ip_created_at", "client_ip", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, {self.method} {self.path} "
            f"{self.status_code} from={self.client_ip})>"
        )
