"""Local human evidence with immutable provenance and a separate reviewer."""
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator
from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.capabilities.external import ArtifactBinding, ExternalRequest, bind_artifacts
from kleos.contracts.models import CheckStatus, DomainError, WireModel
from kleos.projects.store import Base, Membership, fingerprint


class EvidenceAttachment(ArtifactBinding):
    requirement: str = Field(min_length=1, max_length=500)


class EvidenceReading(WireModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=30)
    value: float = Field(allow_inf_nan=False)
    instrument: str = Field(min_length=1, max_length=200)
    method: str = Field(min_length=1, max_length=2000)
    artifact_id: str = Field(min_length=1, max_length=100)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Observation time must include a timezone")
        return value


class EvidenceSubmission(WireModel):
    event_key: str | None = Field(default=None, min_length=1, max_length=150)
    revision: int = Field(ge=1)
    specimen: str = Field(min_length=1, max_length=200)
    specimen_bindings: list[ArtifactBinding] = Field(min_length=1, max_length=100)
    attachments: list[EvidenceAttachment] = Field(min_length=1, max_length=100)
    readings: list[EvidenceReading] = Field(default_factory=list, max_length=100)
    synthetic: bool = False
    notes: str = Field(default="", max_length=4000)


class ReviewRequest(WireModel):
    decision: Literal["accept", "reject", "request_changes"]
    rationale: str = Field(default="", max_length=4000)


class HumanEvidenceRecord(Base):
    __tablename__ = "kleos2_human_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    submitter_id: Mapped[str] = mapped_column(String(100))
    submission: Mapped[dict] = mapped_column(JSON)
    checks: Mapped[list] = mapped_column(JSON)
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def evidence_view(row):
    return {"id": row.id, "jobId": row.job_id, "submitterId": row.submitter_id,
            "submission": row.submission, "checks": row.checks, "review": row.review,
            "physicalAcceptance": False}


class HumanEvidence:
    def __init__(self, jobs):
        self.jobs, self.projects = jobs, jobs.projects

    def list(self, actor, job_id):
        with self.projects.transaction() as session:
            self.jobs._load(session, actor, job_id)
            return [evidence_view(row) for row in session.scalars(select(HumanEvidenceRecord)
                    .where(HumanEvidenceRecord.job_id == job_id).order_by(HumanEvidenceRecord.id).limit(100))]

    def _validate(self, session, job, submission):
        request = ExternalRequest.model_validate(job.request)
        work = request.work_order
        if job.kind != "human_work" or work is None:
            raise DomainError("Job does not request human evidence", "invalid_request", 422)
        if submission.revision != request.revision or submission.specimen != work.specimen:
            raise DomainError("Evidence specimen or revision does not match the work order", "evidence_scope", 422)
        expected = {b.artifact_id: b.model_dump() for b in request.artifact_bindings}
        actual = {b.artifact_id: b.model_dump() for b in submission.specimen_bindings}
        if not expected or expected != actual or len(actual) != len(submission.specimen_bindings):
            raise DomainError("Evidence must identify the exact scoped specimen artifacts", "evidence_scope", 422)
        bind_artifacts(session, job.project_id, submission.specimen_bindings)
        bind_artifacts(session, job.project_id, submission.attachments)
        if set(work.evidence_required) != {a.requirement for a in submission.attachments}:
            raise DomainError("Attach evidence for every work-order requirement", "evidence_incomplete", 422)
        attachments = {a.artifact_id for a in submission.attachments}
        required = {r.name: r for r in work.readings}
        readings = {r.name: r for r in submission.readings}
        if set(required) != set(readings) or len(readings) != len(submission.readings):
            raise DomainError("Provide every required reading exactly once", "evidence_incomplete", 422)
        checks = []
        for name, requirement in required.items():
            reading = readings[name]
            if reading.unit != requirement.unit or reading.artifact_id not in attachments:
                raise DomainError("Reading unit or source artifact does not match", "evidence_provenance", 422)
            if reading.observed_at.timestamp() > self.jobs.clock() + 300:
                raise DomainError("Reading time is in the future", "evidence_provenance", 422)
            status = CheckStatus.PASS
            if requirement.minimum is None and requirement.maximum is None:
                status = CheckStatus.UNKNOWN
            elif ((requirement.minimum is not None and reading.value < requirement.minimum)
                  or (requirement.maximum is not None and reading.value > requirement.maximum)):
                status = CheckStatus.FAIL
            if submission.synthetic:
                status = CheckStatus.UNKNOWN
            checks.append({"name": name, "status": status.value, "artifactId": reading.artifact_id,
                           "independent": False, "scope": "submitted_reading_range"})
        return checks

    def submit(self, actor, job_id, submission: EvidenceSubmission, key):
        body = submission.model_dump(mode="json", by_alias=True)

        def operation(session):
            job, project = self.jobs._load(session, actor, job_id, write=True)
            if submission.event_key is not None:
                previous = session.scalar(select(HumanEvidenceRecord).where(
                    HumanEvidenceRecord.job_id == job_id,
                    HumanEvidenceRecord.submission["eventKey"].as_string() == submission.event_key))
                if previous:
                    if previous.submitter_id != actor.user_id or fingerprint(previous.submission) != fingerprint(body):
                        raise DomainError("Evidence event key already identifies a different submission", "idempotency_conflict")
                    return {"projectId": project.id, "evidence": evidence_view(previous)}
            if job.status in {"cancelled", "expired"}:
                raise DomainError("Job no longer accepts evidence", "job_closed")
            checks = self._validate(session, job, submission)
            row = HumanEvidenceRecord(id=str(uuid4()), job_id=job.id, submitter_id=actor.user_id,
                                      submission=body, checks=checks)
            session.add(row)
            self.projects._emit(session, project, "human.evidence_submitted", {"jobId": job.id, "evidenceId": row.id})
            return {"projectId": project.id, "evidence": evidence_view(row)}
        return self.projects._command(actor, key, {"operation": "human.submit", "jobId": job_id, **body}, operation)

    def review(self, actor, job_id, evidence_id, request: ReviewRequest, key):
        body = request.model_dump(mode="json", by_alias=True)

        def operation(session):
            job, project = self.jobs._load(session, actor, job_id, write=True)
            row = session.scalar(select(HumanEvidenceRecord).where(
                HumanEvidenceRecord.id == evidence_id, HumanEvidenceRecord.job_id == job_id).with_for_update())
            if row is None:
                raise DomainError("Evidence not found", "not_found", 404)
            if row.submitter_id == actor.user_id:
                raise DomainError("Evidence requires a separate reviewer", "independent_review", 403)
            if session.get(Membership, (project.id, actor.user_id)).role != "owner":
                raise DomainError("Evidence review requires a project owner", "forbidden", 403)
            if row.review is not None:
                raise DomainError("Evidence already reviewed; submit a new version", "already_reviewed")
            if job.status in {"cancelled", "expired"}:
                raise DomainError("Job is no longer active", "job_closed")
            submission = EvidenceSubmission.model_validate(row.submission)
            checks = self._validate(session, job, submission)
            if request.decision == "accept" and (submission.synthetic or any(
                    c["status"] != CheckStatus.PASS.value for c in checks)):
                raise DomainError("Evidence is synthetic, failed or lacks a defined acceptance range",
                                  "evidence_not_passed")
            row.review = {**body, "reviewerId": actor.user_id, "reviewedAt": self.jobs.clock(),
                          "independent": True, "physicalAcceptance": False}
            self.projects._emit(session, project, "human.evidence_reviewed", {
                "jobId": job.id, "evidenceId": row.id, "decision": request.decision})
            return {"projectId": project.id, "evidence": evidence_view(row)}
        return self.projects._command(actor, key, {"operation": "human.review", "jobId": job_id,
                                                 "evidenceId": evidence_id, **body}, operation)
