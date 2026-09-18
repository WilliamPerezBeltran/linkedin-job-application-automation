"""SQLAlchemy ORM models — pure persistence mapping, no business rules.

Estas clases mapean columnas de PostgreSQL 1:1 con el esquema documentado en
`CLAUDE.md` ("Modelo de datos (PostgreSQL)"). Las reglas de negocio (qué
transiciones de `status` son válidas, invariantes entre campos, etc.) viven
exclusivamente en `app/domain/entities/**` — este módulo no las conoce ni
las repite; los repositorios (`app/infrastructure/database/repositories/`)
son el único puente entre ambos mundos.

Relaciones (foreign keys) entre las tres tablas del pipeline
`posts/jobs -> job_analysis -> applications`:

* `job_analysis.job_id`   -> `jobs.id` (uno a uno: una fila de análisis por job).
* `applications.job_id`   -> `jobs.id` (uno a uno: el flujo actual del
  ROADMAP genera a lo sumo una `Application` por `Job` — invariante ya
  documentada en el Protocol `ApplicationRepository.get_by_job_id`, ver
  `app/domain/repositories/application_repository.py` — y reforzada aquí con
  un `UniqueConstraint` real, no solo por convención de código. Si en el
  futuro se necesita permitir múltiples intentos de aplicación por job
  (reintentos distintos, no idempotencia), eso requiere una migración
  explícita que levante este constraint, no un cambio implícito de
  comportamiento).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models in this package."""


class JobModel(Base):
    """Maps the `jobs` table.

    `content_hash` es `unique=True` desde el inicio (deduplicación de posts
    repetidos del feed de LinkedIn, ver `CLAUDE.md` "Deduplicación") y tiene
    su propio índice (`unique=True` ya crea uno implícito en PostgreSQL,
    documentado explícitamente aquí para que quede claro que es intencional,
    no un efecto colateral). `status` también se indexa porque el pipeline
    consulta constantemente "todos los jobs en estado X" (ver máquina de
    estados de `CLAUDE.md`).
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    analysis: Mapped[JobAnalysisModel | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    applications: Mapped[list[ApplicationModel]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_jobs_status", "status"),)


class JobAnalysisModel(Base):
    """Maps the `job_analysis` table.

    Clave primaria = `job_id` (relación uno a uno con `jobs`, coherente con
    la entidad de dominio `JobAnalysis`, que no tiene un `id` propio — se
    identifica por el `Job` que analiza). `skills`/`languages`/`frameworks`/
    `cloud` se guardan como `ARRAY(String)` nativo de PostgreSQL — son listas
    planas de strings, sin necesidad de una tabla de detalle separada para
    el alcance actual (YAGNI).
    """

    __tablename__ = "job_analysis"

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    seniority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    frameworks: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    cloud: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    ai_related: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_cv: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generated_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)

    job: Mapped[JobModel] = relationship(back_populates="analysis")


class ApplicationModel(Base):
    """Maps the `applications` table.

    `gmail_draft_id` queda `nullable` hasta que exista (rellenado por
    `gmail-agent` en la Fase 7); `status` se indexa por el mismo motivo que
    en `jobs` (consultas frecuentes por estado, p. ej. "aplicaciones
    pendientes de revisión manual antes de `Send`").

    `job_id` tiene un `UniqueConstraint` (`uq_applications_job_id`) además de
    la `ForeignKey` a `jobs.id`: el flujo actual genera a lo sumo una
    `Application` por `Job` (invariante documentada en el Protocol
    `ApplicationRepository.get_by_job_id`,
    `app/domain/repositories/application_repository.py`, y asumida para
    idempotencia por `CreateGmailDraft` en la capa de aplicación). Sin este
    constraint, dos requests casi simultáneas de creación de draft para el
    mismo job podrían insertar dos filas `applications` antes de que la
    primera haga commit. Si en el futuro el negocio necesita permitir
    reintentos de aplicación distintos para un mismo job, habrá que levantar
    este constraint explícitamente con una migración nueva — no debe
    asumirse ni cambiarse implícitamente.
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cv_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[JobModel] = relationship(back_populates="applications")

    __table_args__ = (
        Index("ix_applications_status", "status"),
        UniqueConstraint("job_id", name="uq_applications_job_id"),
    )
