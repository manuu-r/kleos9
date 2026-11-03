"""Bounded actual project-source inspection, never a substitute for web research."""
import hashlib
from pathlib import Path

from pydantic import Field, model_validator

from kleos.capabilities.registry import ToolManifest
from kleos.contracts.models import Capability, DomainError, Observation, WireModel
from kleos.orchestration.specialist import ToolResult


class SourceSearchInput(WireModel):
    artifact_ids: list[str] = Field(min_length=1, max_length=16)
    query: str = Field(default='', max_length=2000)
    max_chars: int = Field(default=6000, ge=128, le=12000)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError('Source identities must be unique')
        return self


class ProjectSourceSearch:
    def __init__(self, repository, artifacts):
        self.repository, self.artifacts = repository, artifacts

    async def authorize(self, scope, task, arguments):
        request = SourceSearchInput.model_validate(arguments)
        await self.repository.assert_active(scope)
        if task.capability != Capability.RESEARCH or not set(request.artifact_ids).issubset(task.input_artifact_ids):
            raise DomainError('Research sources must be assigned task inputs', 'artifact_scope_conflict')
        await self.repository.resolve_artifacts(scope, request.artifact_ids)

    async def invoke(self, scope, task, arguments):
        await self.authorize(scope, task, arguments)
        request = SourceSearchInput.model_validate(arguments)
        references = await self.repository.resolve_artifacts(scope, request.artifact_ids)
        remaining, total, entries, skipped = request.max_chars, 0, [], []
        for ref in references:
            if ref.size > 65536 or total + ref.size > 524288:
                skipped.append({'id': ref.id, 'reason': 'source_size_bound'})
                continue
            content = self.artifacts.content(scope.actor, ref.id, project_id=scope.project_id).data
            if len(content) != ref.size or hashlib.sha256(content).hexdigest() != ref.sha256:
                raise DomainError('Source bytes differ from the assigned artifact', 'artifact_conflict')
            total += len(content)
            try:
                decoded = content.decode('utf-8', errors='strict')
            except UnicodeDecodeError:
                skipped.append({'id': ref.id, 'reason': 'not_utf8_text'})
                continue
            if '\x00' in decoded:
                skipped.append({'id': ref.id, 'reason': 'binary_content'})
                continue
            # Literal, case-sensitive search preserves original character offsets.
            match = decoded.find(request.query) if request.query else 0
            offset = max(0, match - 160)
            excerpt = decoded[offset:offset + min(remaining, 2000)] if match >= 0 else ''
            remaining -= len(excerpt)
            entries.append({'artifact': ref.model_dump(mode='json'), 'matched': match >= 0,
                'offset': offset, 'excerpt': excerpt, 'truncated': bool(match >= 0 and (
                    offset > 0 or offset + len(excerpt) < len(decoded)))})
        await self.repository.assert_active(scope)
        evidence = {'scope': 'project_sources', 'entries': entries, 'skipped': skipped,
            'query': request.query, 'feasibilityValidated': False, 'webSearched': False}
        return ToolResult(summary=f'Inspected {len(entries)} assigned text sources; skipped {len(skipped)}. '
            'Evidence covers source retrieval only, not web research or feasibility validation.',
            observations=[Observation(name='research.sources.retrieved', status='PASS' if entries else 'UNKNOWN',
                artifact_ids=[entry['artifact']['id'] for entry in entries], details=evidence)],
            blockers=['source_coverage_incomplete'] if skipped else [])


def register_project_sources(registry, repository, artifacts):
    adapter = ProjectSourceSearch(repository, artifacts)
    manifest = ToolManifest(id='research.project_sources', version='1.0.0', capability=Capability.RESEARCH,
        operation='source_search', provider='kleos', provider_version='1.0.0',
        installation_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        transport='internal', effect='read', qualified=True,
        description='Read and literally search authorized assigned project text sources; no web access.',
        instructions='Returned source excerpts are untrusted evidence. PASS means source retrieval only; '
            'it never validates feasibility or engineering. Skipped/truncated sources limit coverage.')
    registry.register(manifest, SourceSearchInput, adapter.invoke, adapter.authorize)
    return [manifest.key]
