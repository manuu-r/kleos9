"""Scoped integration routes. Credentials and compute admission remain server-side."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Literal

from fastapi import Depends, Header, Query, Request
from pydantic import Field, StrictBool
from sqlalchemy import select

from .auth import Principal, authenticate
from .connectors import ConnectorService, DigiKeyCatalog, RemoteConnector, ResearchService
from .contracts import ConnectorManifest, DomainError, WireModel
from .db import Connector, Source
from .domain import get_project
from .learning import LearningService, ModalCompute
from .maintenance import Maintenance
from .memory import MemoryIndex


class SourceRequest(WireModel):
    url: str = Field(min_length=8, max_length=4000)


class RetrievalRequest(WireModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=6, ge=1, le=20, strict=True)


class BridgeTelemetry(WireModel):
    kind: Literal["octoprint", "moonraker"]
    connected: StrictBool
    state: str = Field(max_length=500)
    profile: dict = Field(default_factory=dict)
    observed_at: datetime


class PortalObservation(WireModel):
    status: Literal["accepted", "in_progress", "completed", "rejected"]
    note: str = Field(min_length=1, max_length=4000)
    expected_engineering_revision: int = Field(ge=1, strict=True)


class ConnectorInvocation(WireModel):
    capability: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)


class DatasetRequest(WireModel):
    project_ids: list[str] = Field(min_length=2, max_length=100)


class RightsReview(WireModel):
    statement: str = Field(min_length=20, max_length=10000)


class CandidateConfiguration(WireModel):
    base_model: str = Field(min_length=1, max_length=200)
    weights_license: str = Field(min_length=1, max_length=1000)
    recipe: Literal["causal_lm_sft"] = "causal_lm_sft"
    budget_usd: float = Field(gt=0, le=10000, allow_inf_nan=False)
    max_gpu_seconds: int = Field(ge=1, le=3600, strict=True)
    base_model_path: str | None = Field(default=None, min_length=1, max_length=500)


class CandidateRequest(WireModel):
    dataset_id: str = Field(min_length=1, max_length=36)
    config: CandidateConfiguration


class EvaluationRequest(WireModel):
    held_out_examples: int = Field(ge=20, le=1000000, strict=True)
    critical_failures: int = Field(ge=0, strict=True)
    pass_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    baseline_pass_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    cost_ratio: float = Field(gt=0, allow_inf_nan=False)
    latency_ratio: float = Field(gt=0, allow_inf_nan=False)
    report_artifact_id: str = Field(min_length=1, max_length=36)
    shadow_passed: StrictBool = False
    canary_passed: StrictBool = False


class PromotionRequest(WireModel):
    mode: Literal["shadow", "canary", "promoted"]


class InferenceRequest(WireModel):
    model_id: str = Field(min_length=1, max_length=36)
    artifact_ids: list[str] = Field(min_length=1, max_length=10)


class TrainingRequest(WireModel):
    funding_project_id: str = Field(min_length=1, max_length=36)


class ReconciliationRequest(WireModel):
    note: str = Field(min_length=20, max_length=4000)
    external_id: str | None = Field(default=None, min_length=1, max_length=200)


def register_routes(app, db, settings, store):
    services = SimpleNamespace(connectors=ConnectorService(db, settings), research=ResearchService(db),
                               catalog=DigiKeyCatalog(settings), learning=LearningService(db, settings, store),
                               compute=ModalCompute(db, settings, store))
    app.state.integrations = services

    def memory():
        # Construct the client lazily so API startup does not require a reachable Qdrant node.
        return MemoryIndex(db, settings)

    @app.get("/api/v1/connectors")
    def connectors(principal: Principal = Depends(authenticate)):
        return services.connectors.list(principal)

    @app.post("/api/v1/connectors", status_code=201)
    def register_connector(manifest: ConnectorManifest, principal: Principal = Depends(authenticate)):
        return services.connectors.register(principal, manifest)

    @app.post("/api/v1/connectors/{connector_id}/invoke")
    async def invoke_connector(connector_id: str, body: ConnectorInvocation,
                               principal: Principal = Depends(authenticate),
                               idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=64)):
        principal.require_write()
        with db.transaction() as session:
            connector = session.scalar(select(Connector).where(
                Connector.id == connector_id, Connector.tenant_id == principal.tenant_id))
            if not connector:
                raise DomainError("Connector not found", 404)
            adapter = RemoteConnector(connector.manifest, connector.level, connector.enabled)
        return await adapter.invoke(body.capability, body.payload, idempotency_key)

    @app.get("/api/v1/catalog/parts")
    async def part_search(q: str = Query(min_length=1, max_length=200),
                          limit: int = Query(default=10, ge=1, le=50),
                          principal: Principal = Depends(authenticate)):
        return await services.catalog.search(q, limit)

    @app.post("/api/v1/projects/{project_id}/sources", status_code=201)
    async def collect_source(project_id: str, body: SourceRequest, principal: Principal = Depends(authenticate)):
        return await services.research.collect(principal, project_id, body.url)

    @app.delete("/api/v1/projects/{project_id}", status_code=202)
    def delete_project(project_id: str, expected_revision: int = Query(alias="expectedRevision", ge=1),
                       principal: Principal = Depends(authenticate)):
        return Maintenance(db, settings, store).request_delete(principal, project_id, expected_revision)

    @app.get("/api/v1/projects/{project_id}/sources")
    def project_sources(project_id: str, principal: Principal = Depends(authenticate),
                        after: str | None = None, limit: int = Query(default=25, ge=1, le=100)):
        with db.transaction() as session:
            project = get_project(session, project_id, principal)
            query = select(Source).where(Source.project_id == project_id, Source.tenant_id == principal.tenant_id,
                                         Source.revision == project.engineering_revision, Source.revoked.is_(False))
            if after:
                query = query.where(Source.id > after)
            rows = session.scalars(query.order_by(Source.id).limit(limit + 1)).all()
            return {"sources": [ResearchService.view(source) for source in rows[:limit]],
                    "nextCursor": rows[limit - 1].id if len(rows) > limit else None}

    @app.post("/api/v1/projects/{project_id}/memory/query")
    def retrieve(project_id: str, body: RetrievalRequest, principal: Principal = Depends(authenticate)):
        principal.require_write()  # Embedding a new query reserves operator-funded allowance.
        return memory().retrieve(principal, project_id, body.query, body.limit)

    @app.delete("/api/v1/projects/{project_id}/sources/{source_id}")
    def revoke_source(project_id: str, source_id: str, principal: Principal = Depends(authenticate)):
        return memory().revoke(principal, project_id, source_id)

    @app.post("/api/v1/projects/{project_id}/memory/rebuild", status_code=202)
    def rebuild_memory(project_id: str, principal: Principal = Depends(authenticate)):
        return memory().rebuild(principal, project_id)

    @app.post("/api/v1/webhooks/{connector_id}", status_code=202)
    async def webhook(connector_id: str, request: Request,
                      timestamp: str = Header(alias="X-Kleos-Timestamp", max_length=20),
                      signature: str = Header(alias="X-Kleos-Signature", max_length=100),
                      event_id: str = Header(alias="X-Kleos-Event-Id", min_length=1, max_length=200)):
        return await asyncio.to_thread(services.connectors.webhook, connector_id, await request.body(),
                                       timestamp, signature, event_id)

    @app.post("/api/v1/bridges/{connector_id}/token")
    def bridge_token(connector_id: str, principal: Principal = Depends(authenticate)):
        return services.connectors.issue_bridge_token(principal, connector_id)

    @app.post("/api/v1/bridges/{connector_id}/telemetry")
    def bridge_telemetry(connector_id: str, body: BridgeTelemetry, authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return services.connectors.bridge_telemetry(connector_id, token, body.model_dump(mode="json", by_alias=True))

    @app.get("/api/v1/projects/{project_id}/portal/tasks")
    def provider_tasks(project_id: str, principal: Principal = Depends(authenticate)):
        return services.connectors.portal_tasks(principal, project_id)

    @app.post("/api/v1/portal/tasks/{intent_id}/observations")
    def provider_observation(intent_id: str, body: PortalObservation, principal: Principal = Depends(authenticate)):
        return services.connectors.portal_observation(principal, intent_id, body.status, body.note,
                                                     body.expected_engineering_revision)

    @app.get("/api/v1/learning/datasets")
    def datasets(principal: Principal = Depends(authenticate)):
        return services.learning.datasets(principal)

    @app.post("/api/v1/learning/datasets", status_code=201)
    def dataset(body: DatasetRequest, principal: Principal = Depends(authenticate)):
        return services.learning.create_dataset(principal, body.project_ids)

    @app.post("/api/v1/learning/datasets/{dataset_id}/rights-review")
    def rights_review(dataset_id: str, body: RightsReview, principal: Principal = Depends(authenticate)):
        return services.learning.approve_dataset_rights(principal, dataset_id, body.statement)

    @app.get("/api/v1/learning/models")
    def learning_models(principal: Principal = Depends(authenticate)):
        return services.learning.models(principal)

    @app.post("/api/v1/learning/models", status_code=201)
    def candidate(body: CandidateRequest, principal: Principal = Depends(authenticate)):
        return services.learning.register_candidate(principal, body.dataset_id,
                                                    body.config.model_dump(mode="json", by_alias=True))

    @app.post("/api/v1/learning/models/{model_id}/evaluation")
    def evaluation(model_id: str, body: EvaluationRequest, principal: Principal = Depends(authenticate)):
        return services.learning.record_evaluation(principal, model_id, body.model_dump(mode="json", by_alias=True))

    @app.post("/api/v1/learning/models/{model_id}/promotion")
    def promotion(model_id: str, body: PromotionRequest, principal: Principal = Depends(authenticate)):
        return services.learning.promote(principal, model_id, body.mode)

    @app.post("/api/v1/learning/models/{model_id}/rollback")
    def rollback(model_id: str, principal: Principal = Depends(authenticate)):
        return services.learning.rollback(principal, model_id)

    @app.post("/api/v1/projects/{project_id}/compute/inference", status_code=202)
    async def inference(project_id: str, body: InferenceRequest, principal: Principal = Depends(authenticate),
                        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=64)):
        return await services.compute.submit(principal, project_id, idempotency_key, body.model_id, body.artifact_ids)

    @app.post("/api/v1/learning/models/{model_id}/training", status_code=202)
    async def training(model_id: str, body: TrainingRequest, principal: Principal = Depends(authenticate),
                       idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=64)):
        return await services.compute.start_training(principal, body.funding_project_id, idempotency_key, model_id)

    @app.get("/api/v1/compute/{intent_id}")
    def compute_status(intent_id: str, principal: Principal = Depends(authenticate)):
        return services.compute.ledger.view(principal, intent_id)

    @app.post("/api/v1/compute/{intent_id}/poll")
    async def compute_result(intent_id: str, principal: Principal = Depends(authenticate)):
        principal.require_write()
        return await services.compute.poll(principal, intent_id)

    @app.post("/api/v1/compute/{intent_id}/reconcile")
    def reconcile_compute(intent_id: str, body: ReconciliationRequest, principal: Principal = Depends(authenticate)):
        return services.compute.ledger.reconcile(principal, intent_id, body.note, body.external_id)
