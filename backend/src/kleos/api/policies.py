"""Explicit project provider choices; existing objective pins are immutable."""

from kleos.api.catalog import LANES
from kleos.contracts.models import DomainError
from kleos.orchestration.models import ModelConfiguration


class PolicyService:
    def __init__(self, projects, settings):
        self.projects, self.settings = projects, settings

    def configurations(self):
        return [ModelConfiguration.model_validate(item) for item in self.settings.model_catalog]

    def resolve(self, value):
        if not isinstance(value, dict) or len(value) > len(LANES) or set(value) - set(LANES):
            raise DomainError("Choose models for known engineering lanes", "invalid_model_policy", 422)
        normalized, keys = {}, {}
        catalog = self.configurations()
        credentials = {"meta": self.settings.meta_api_key, "modal": self.settings.modal_api_key}
        for lane, choice in value.items():
            if not isinstance(choice, dict) or set(choice) != {"provider", "model"}:
                raise DomainError("A model choice requires provider and model", "invalid_model_policy", 422)
            matches = [
                item
                for item in catalog
                if item.provider == choice["provider"] and item.model == choice["model"]
            ]
            if len(matches) != 1:
                raise DomainError("Selected model is unavailable or ambiguous", "model_unavailable")
            item = matches[0]
            key = credentials[item.provider]
            if not item.qualified or not item.tool_calls or not key or not key.get_secret_value():
                raise DomainError("Selected model is not configured and qualified", "model_unavailable")
            if lane == "coordination" and not item.vision:
                raise DomainError("Capture requires an image-capable model", "vision_model_required")
            normalized[lane] = {"provider": item.provider, "model": item.model}
            keys[lane] = item.key
        return normalized, keys

    def validate(self, value):
        return self.resolve(value)[0]

    def update(self, actor, project_id, value, expected_revision, key):
        normalized = self.validate(value)

        def operation(session):
            project = self.projects.authorized(session, actor, project_id, write=True)
            if project.revision != expected_revision:
                raise DomainError("Project changed; refresh before changing providers", "revision_conflict")
            project.advance_revision(context=False)
            project.data = {
                **project.data,
                "modelPolicy": normalized,
                "policyRevision": project.data.get("policyRevision", 1) + 1,
            }
            self.projects._emit(
                session,
                project,
                "project.modelPolicyChanged",
                {
                    "modelPolicy": normalized,
                    "policyRevision": project.data["policyRevision"],
                },
            )
            return {"projectId": project.id, "revision": project.revision}

        return self.projects._command(
            actor,
            key,
            {
                "type": "project.setModelPolicy",
                "projectId": project_id,
                "modelPolicy": normalized,
                "expectedRevision": expected_revision,
            },
            operation,
        )
