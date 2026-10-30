"""Rebuildable retrieval index. PostgreSQL authorization and applicability remain authoritative."""

import hashlib
import math

from qdrant_client import QdrantClient, models
from sqlalchemy import select

from .auth import Principal
from .contracts import DomainError
from .db import ExternalIntent, Outbox, Project, Source, now, uid
from .domain import emit, get_project
from .learning import ComputeLedger
from .telemetry import tracer


def embedding_version(model):
    # Input formatting is part of vector/cache identity, just like the provider model.
    return f"{model}:retrieval-v2"


class GeminiEmbedder:
    def __init__(self, settings):
        self.settings = settings

    def embed(self, content, query=False):
        if not self.settings.gemini_api_key:
            raise DomainError("Gemini embeddings are not configured", 503, "provider_unconfigured")
        from google import genai
        from google.genai import types
        model = self.settings.embedding_model.removeprefix("models/")
        # auto_truncate belongs to the enterprise API and is rejected by the direct API SDK.
        config = {"output_dimensionality": 768}
        if model == "gemini-embedding-2":
            content = f"task: search result | query: {content}" if query else f"title: none | text: {content}"
        elif model == "gemini-embedding-001":
            config["task_type"] = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
        else:
            raise DomainError("Embedding model has no qualified input format", 503, "model_unqualified")
        with genai.Client(api_key=self.settings.gemini_api_key.get_secret_value(), http_options=types.HttpOptions(
                timeout=self.settings.model_timeout_seconds * 1000,
                retry_options=types.HttpRetryOptions(attempts=3))) as client:
            response = client.models.embed_content(
                model=self.settings.embedding_model, contents=content,
                config=types.EmbedContentConfig(**config),
            )
        if not response.embeddings or not response.embeddings[0].values:
            raise DomainError("Embedding provider returned no vector", 502)
        statistics = getattr(response.embeddings[0], "statistics", None)
        if statistics and statistics.truncated:
            raise DomainError("Embedding provider truncated the source; shorten the document chunk", 502)
        return response.embeddings[0].values


class MemoryIndex:
    def __init__(self, db, settings, embedder=None, client=None):
        self.db, self.settings = db, settings
        self.embedder = embedder or GeminiEmbedder(settings)
        self.client = client or QdrantClient(url=settings.qdrant_url,
            api_key=settings.qdrant_key.get_secret_value() if settings.qdrant_key else None, timeout=15,
            check_compatibility=False)
        self.ledger = ComputeLedger(db, settings)
        self.embedding_version = embedding_version(settings.embedding_model)
        version = hashlib.sha256(self.embedding_version.encode()).hexdigest()[:12]
        self.collection = f"kleos_sources_{version}_v1"
        self.alias = f"kleos_sources_{version}"

    def ensure_collection(self, size=768):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=size, distance=models.Distance.COSINE))
            for field in ("tenantId", "projectId", "authority"):
                self.client.create_payload_index(self.collection, field, models.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(self.collection, "revision", models.PayloadSchemaType.INTEGER)
        aliases = {a.alias_name for a in self.client.get_aliases().aliases}
        if self.alias not in aliases:
            self.client.update_collection_aliases([models.CreateAliasOperation(
                create_alias=models.CreateAlias(collection_name=self.collection, alias_name=self.alias))])

    def _embed(self, principal, project_id, text, query, revision):
        if not isinstance(text, str) or not text or len(text) > 6000:
            raise DomainError("Embedding input must be bounded to 6000 characters", 422)
        # Published direct-API text rates; use the full provider token ceiling and all
        # three possible transport attempts, not a guessed text-to-token conversion.
        rates = {"gemini-embedding-2": (8192, .20), "gemini-embedding-001": (2048, .15)}
        model = self.settings.embedding_model.removeprefix("models/")
        if model not in rates:
            raise DomainError("Embedding model has no qualified allowance policy", 503, "model_unqualified")
        token_ceiling, usd_per_million = rates[model]
        reserve = self.settings.embedding_reservation_usd
        if reserve < token_ceiling * usd_per_million * 3 / 1_000_000:
            raise DomainError("Embedding allowance is below the qualified maximum request cost", 409,
                              "embedding_budget_unconfigured")
        if isinstance(self.embedder, GeminiEmbedder) and not self.settings.gemini_api_key:
            raise DomainError("Gemini embeddings are not configured", 503, "provider_unconfigured")
        fingerprint = hashlib.sha256((self.embedding_version + ":" + str(query) + ":" + text).encode()).hexdigest()
        with self.db.transaction() as session:
            get_project(session, project_id, principal)
            prior = session.scalar(select(ExternalIntent).where(
                ExternalIntent.project_id == project_id, ExternalIntent.revision == revision,
                ExternalIntent.operation == f"embedding:{fingerprint}"))
            generation = prior.receipt.get("retryGeneration") if prior and prior.status == "failed" else None
            if generation:
                fingerprint = hashlib.sha256(f"{fingerprint}:{generation}".encode()).hexdigest()
        intent_id, fresh = self.ledger.begin(principal, project_id, fingerprint, "embedding",
                                            {"textSha256": hashlib.sha256(text.encode()).hexdigest(),
                                             "model": self.settings.embedding_model, "query": query,
                                             "embeddingVersion": self.embedding_version,
                                             "pricingVersion": "google-embeddings-2026-10-30",
                                             "maximumInputTokens": token_ceiling, "maximumTransportAttempts": 3,
                                             "revision": revision}, reserve=reserve)
        if not fresh:
            cached = self.ledger.view(principal, intent_id)
            if cached["status"] != "succeeded" or not cached["receipt"].get("vector"):
                raise DomainError("Embedding attempt failed; an administrator must request a memory rebuild", 409,
                                  "embedding_retry_required")
            return cached["receipt"]["vector"]
        try:
            with tracer.start_as_current_span("model.embed", attributes={
                    "gen_ai.provider.name": "google", "gen_ai.request.model": self.settings.embedding_model,
                    "compute.intent_id": intent_id}):
                vector = self.embedder.embed(text, query=query)
            if len(vector) != 768 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                raise DomainError("Embedding vector does not match the qualified collection", 502)
            self.ledger.finish(intent_id, {"vector": vector, "model": self.settings.embedding_model})
            return vector
        except Exception:
            self.ledger.uncertain(intent_id)
            raise

    def index(self, source_id):
        with self.db.transaction() as session:
            source = session.get(Source, source_id)
            if not source:
                return
            project = session.get(Project, source.project_id)
            if source.revoked or not project or project.deleted_at:
                self.delete_points([source_id])
                return
            if source.revision != project.engineering_revision:
                return
            principal = Principal(project.owner_id, project.tenant_id, "member")
            # Chunking is deterministic and immutable. Large documents are indexed as source chunks.
            chunks = [source.text[i:i + 6000] for i in range(0, len(source.text), 6000)]
            payload = {"tenantId": source.tenant_id, "projectId": source.project_id,
                       "revision": source.revision, "sourceId": source.id, "sha256": source.sha256,
                       "authority": source.authority, "embeddingModel": self.settings.embedding_model}
        points = []
        import uuid
        for offset, chunk in enumerate(chunks):
            vector = self._embed(principal, source.project_id, chunk, False, source.revision)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"kleos:{source.id}:{offset}:{source.sha256}"))
            points.append(models.PointStruct(id=point_id, vector=vector,
                                             payload={**payload, "chunk": offset}))
        self.ensure_collection()
        if points:
            self.client.upsert(collection_name=self.alias, points=points, wait=True)
        with self.db.transaction() as session:
            current = session.get(Source, source_id)
            project = session.get(Project, current.project_id) if current else None
            if not current or current.revoked or not project or project.deleted_at or (
                    current.revision != project.engineering_revision):
                self.delete_points([source_id])
                return
            current.indexed_at = now()

    def retrieve(self, principal, project_id, query, limit=6):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or not 1 <= limit <= 20:
            raise DomainError("Provide a short retrieval query and limit between 1 and 20", 422)
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal)
            revision = project.engineering_revision
        vector = self._embed(principal, project_id, query, True, revision)
        self.ensure_collection()
        result = self.client.query_points(collection_name=self.alias, query=vector, limit=limit * 3,
            query_filter=models.Filter(must=[
                models.FieldCondition(key="tenantId", match=models.MatchValue(value=principal.tenant_id)),
                models.FieldCondition(key="projectId", match=models.MatchValue(value=project_id)),
                models.FieldCondition(key="revision", match=models.MatchValue(value=revision)),
            ]), with_payload=True)
        rows, seen = [], set()
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal)
            if project.engineering_revision != revision:
                raise DomainError("Project revision changed during retrieval", 409, "stale_revision")
            for point in result.points:
                payload = point.payload or {}
                source = session.get(Source, payload.get("sourceId"))
                if not source or source.id in seen or source.revoked or source.tenant_id != principal.tenant_id or (
                        source.project_id != project_id or source.revision != revision or
                        source.sha256 != payload.get("sha256") or source.authority != payload.get("authority")):
                    continue
                chunk = max(0, int(payload.get("chunk", 0)))
                rows.append({"sourceId": source.id, "uri": source.uri, "sha256": source.sha256,
                             "authority": source.authority, "score": point.score,
                             "text": source.text[chunk * 6000:(chunk + 1) * 6000],
                             "observedAt": source.created_at.isoformat(), "revision": revision})
                seen.add(source.id)
                if len(rows) >= limit:
                    break
        return {"sources": rows, "revision": revision, "embeddingModel": self.settings.embedding_model}

    def revoke(self, principal, project_id, source_id):
        principal.require_write()
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            source = session.scalar(select(Source).where(Source.id == source_id, Source.project_id == project_id,
                                                          Source.tenant_id == principal.tenant_id))
            if not source:
                raise DomainError("Source not found", 404)
            if source.revoked:
                return {"sourceId": source_id, "revoked": True}
            source.revoked = True
            session.add(Outbox(key=f"revoke:{source_id}", kind="delete_source", payload={"sourceId": source_id}))
            emit(session, project, "research.sourceRevoked", {"sourceId": source.id})
        # DB revocation blocks retrieval immediately; outbox deletion handles Qdrant outages.
        return {"sourceId": source_id, "revoked": True}

    def delete_points(self, source_ids):
        if self.client.collection_exists(self.collection):
            self.client.delete(collection_name=self.collection, points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(
                    key="sourceId", match=models.MatchAny(any=source_ids))])), wait=True)

    def rebuild(self, principal, project_id):
        principal.require_admin()
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            generation = uid()
            for failed in session.scalars(select(ExternalIntent).where(
                    ExternalIntent.project_id == project_id, ExternalIntent.revision == project.engineering_revision,
                    ExternalIntent.operation.like("embedding:%"), ExternalIntent.status == "failed")):
                failed.receipt = {**failed.receipt, "retryGeneration": generation}
            sources = session.scalars(select(Source).where(
                Source.project_id == project_id, Source.tenant_id == principal.tenant_id,
                Source.revision == project.engineering_revision, Source.revoked.is_(False))).all()
            for source in sources:
                session.add(Outbox(key=f"reindex:{source.id}:{uid()}", kind="index_source",
                                   payload={"sourceId": source.id}))
            emit(session, project, "memory.rebuildRequested", {"actorId": principal.subject, "queued": len(sources)})
            return {"queued": len(sources)}
