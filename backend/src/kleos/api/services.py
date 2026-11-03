"""Application composition. Unsupported modules are explicit until wired and qualified."""

from sqlalchemy import select

from kleos.api.external import summary as external_summary
from kleos.capabilities.external import ExternalJob
from kleos.contracts.models import DomainError, ObjectiveRequest
from kleos.projects.views import ProjectViews


class ApplicationServices:
    def __init__(
        self,
        projects,
        access,
        *,
        artifacts=None,
        memory=None,
        worker=None,
        bambu=None,
        external=None,
        catalog=None,
        design=None,
        policies=None,
        releases=None,
        fabrication=None,
        engineering=None,
        interfaces=None,
        previews=None,
        connectors=None,
        human_preview=None,
        research=None,
        tool_policies=None,
        bambu_test_print=None,
        archive_import=None,
        repository_import=None,
        workbench_edits=None,
        workbench_progress=None,
        companion=None,
        prerequisites=None,
    ):
        self.projects, self.access = projects, access
        self.views = ProjectViews(projects, artifacts=artifacts,
            external_jobs=self._external_summaries if external else None)
        self.artifacts, self.memory = artifacts, memory
        self.worker, self.bambu = worker, bambu
        self.external = external
        self.catalog = catalog
        self.design = design
        self.policies = policies
        self.releases, self.fabrication = releases, fabrication
        self.engineering = engineering
        self.interfaces = interfaces
        self.previews = previews
        self.connectors = connectors
        self.human_preview = human_preview
        self.research = research
        self.tool_policies = tool_policies
        self.bambu_test_print = bambu_test_print
        self.archive_import = archive_import
        self.repository_import = repository_import
        self.workbench_edits = workbench_edits
        self.workbench_progress = workbench_progress
        self.companion = companion
        self.prerequisites = prerequisites

    def _external_summaries(self, session, project):
        return [external_summary(job, project, self.external.jobs.clock()) for job in session.scalars(
            select(ExternalJob).where(ExternalJob.project_id == project.id,
                                     ExternalJob.tenant_id == project.tenant_id).order_by(ExternalJob.id))]

    def authenticate(self, headers, cookies, method):
        return self.access.authenticate(headers, cookies, method)

    def auth(self, operation, body, headers, cookies):
        return self.access.auth(operation, body, headers, cookies)

    def query(self, actor, operation, params):
        if self.bambu_test_print:
            from kleos.api.bambu_test_print import QUERIES
            if operation in QUERIES:
                return self.bambu_test_print.query(actor, operation, params)
        if self.human_preview and operation == 'human_task_drafts':
            return self.human_preview.query(actor, operation, params)
        if operation == "connectors" and self.connectors:
            return self.connectors.query(actor, params)
        if self.previews:
            from kleos.api.previews import QUERIES

            if operation in QUERIES:
                return self.previews.query(actor, operation, params)
        if self.fabrication:
            from kleos.api.fabrication import QUERIES

            if operation in QUERIES:
                return self.fabrication.query(actor, operation, params)
        if self.catalog and operation in {"model_catalog", "engineering_tools"}:
            return self.catalog.query(operation)
        if self.external:
            from kleos.api.external import QUERIES

            if operation in QUERIES:
                return self.external.query(actor, operation, params)
        if operation.startswith("bambu_") and self.bambu:
            return self.bambu.query(actor, operation, params)
        if operation == "projects":
            return self.views.list(actor, params.get("cursor"))
        if operation == "workspace":
            return self.views.workspace(actor, params["projectId"])
        if operation == "agents":
            return self.views.agents(actor, params["projectId"], after=params.get("after"), limit=params.get("limit", 25))
        if operation == "run":
            return self.views.run(actor, params["runId"])
        if operation == "native_logs":
            return self.views.native_logs(actor, params["jobId"], self.access)
        if operation == "invocation_transcript":
            from kleos.api.model_records import model_record
            return model_record(self.projects, self.access, actor, params["invocationId"])
        if operation in {"events", "event_history"}:
            return self.views.events(actor, params["projectId"], params.get("after", 0))
        if operation == "artifact_download" and self.artifacts:
            return self.artifacts.download(actor, params["artifactId"])
        if operation in {"members", "invites"}:
            return self.access.query(actor, operation, params)
        raise DomainError(
            "This capability is not available in the replacement yet", "capability_unavailable", 501
        )

    def command(self, actor, body, idempotency_key):
        if body['type'] == 'project.cancelRepositoryImport' and self.repository_import:
            payload = body.get('payload', {})
            if not isinstance(payload, dict) or set(payload) != {'projectId', 'importKey'}:
                raise DomainError('Provide the project and original import key', 'invalid_request', 422)
            return self.repository_import.cancel(actor, payload['projectId'], payload['importKey'],
                body.get('expectedRevision'), idempotency_key)
        if body['type'] == 'project.importRepository' and self.repository_import:
            payload = dict(body.get('payload', {}))
            project_id = payload.pop('projectId', None)
            return self.repository_import.import_repository(actor, project_id, payload, idempotency_key,
                expected_revision=body.get('expectedRevision'))
        if body['type'] in {'project.selectPrint', 'project.assemblyStep'} and self.workbench_progress:
            return self.workbench_progress.command(actor, body, idempotency_key)
        if body['type'] in {'project.toggleFeature', 'project.swapBattery'} and self.workbench_edits:
            return self.workbench_edits.command(actor, body, idempotency_key)
        if body['type'] == 'project.importArchive' and self.archive_import:
            payload = dict(body.get('payload', {}))
            project_id = payload.pop('projectId', None)
            return self.archive_import.import_archive(actor, project_id, payload, idempotency_key,
                expected_revision=body.get('expectedRevision'))
        if self.prerequisites is not None:
            reply = self.prerequisites.dispatch(actor, body, idempotency_key)
            if reply is not None:
                return reply
        if self.interfaces and self.interfaces.handles(actor, body):
            return self.interfaces.command(actor, body, idempotency_key)
        if self.design and self.design.handles(actor, body):
            return self.design.command(actor, body, idempotency_key)
        kind, payload = body["type"], body.get("payload", {})
        if kind in {'project.selectTool', 'project.setToolPolicy'} and self.tool_policies:
            return self.tool_policies.command(actor, body, idempotency_key)
        if kind == 'project.research' and self.research:
            return self.research.command(actor, body, idempotency_key)
        if kind == 'project.setTrainingConsent':
            from kleos.projects.consent import set_training_consent
            return set_training_consent(self.projects, actor, body, idempotency_key)
        if self.engineering:
            from kleos.api.engineering import COMMANDS
            if kind in COMMANDS:
                return self.engineering.command(actor, body, idempotency_key)
        if kind == "project.acceptRelease" and self.releases:
            return self.releases.accept(actor, body, idempotency_key)
        if kind == "project.setModelPolicy" and self.policies:
            return self.policies.update(
                actor,
                payload.get("projectId"),
                payload.get("modelPolicy"),
                body.get("expectedRevision"),
                idempotency_key,
            )
        if self.external:
            from kleos.api.external import COMMANDS

            if kind in COMMANDS:
                return self.external.command(actor, body, idempotency_key)
        if kind == "project.create":
            if payload.get("autoStart"):
                raise DomainError("Automatic capture is not connected yet", "capability_unavailable", 501)
            model_policy = None
            if "modelPolicy" in payload:
                if not self.policies:
                    raise DomainError("Provider configuration is unavailable", "model_unavailable")
                model_policy = self.policies.validate(payload["modelPolicy"])
            return self.projects.create(
                actor, payload.get("brief"), idempotency_key, model_policy=model_policy
            )
        if kind in {"project.objective", "project.message"}:
            if payload.get("runId") and self.worker:
                return self.worker.answer(
                    actor,
                    payload.get("projectId"),
                    payload["runId"],
                    payload.get("interruptId"),
                    payload.get("text", payload.get("message", "")),
                    body.get("expectedRevision"),
                    idempotency_key,
                )
            request = ObjectiveRequest(
                project_id=payload.get("projectId"),
                objective=payload.get("objective", payload.get("text", payload.get("message", ""))),
                expected_revision=body.get("expectedRevision"),
                idempotency_key=idempotency_key,
            )
            return self.projects.submit(
                actor,
                request.project_id,
                request.objective,
                request.expected_revision,
                request.idempotency_key,
            )
        if kind == "project.objective.answer" and self.worker:
            return self.worker.answer(
                actor,
                payload.get("projectId"),
                payload.get("runId"),
                payload.get("interruptId"),
                payload.get("answer"),
                body.get("expectedRevision"),
                idempotency_key,
            )
        if kind == "project.run.cancel":
            return self.projects.cancel(
                actor,
                payload.get("projectId"),
                payload.get("runId"),
                body.get("expectedRevision"),
                idempotency_key,
            )
        raise DomainError("This command is not connected yet", "capability_unavailable", 501)

    def action(self, actor, operation, params, body, idempotency_key):
        if operation == 'delete_project':
            from kleos.projects.lifecycle import delete_project
            return delete_project(self.projects, actor, params, body, idempotency_key)
        if operation == 'companion_preview' and self.companion:
            return self.companion.issue(actor, params['projectId'], body)
        if self.bambu_test_print:
            from kleos.api.bambu_test_print import ACTIONS
            if operation in ACTIONS:
                return self.bambu_test_print.action(actor, operation, params, body, idempotency_key)
        if self.human_preview and operation == 'human_draft_preview':
            return self.human_preview.action(actor, operation, params, body, idempotency_key)
        if self.fabrication:
            from kleos.api.fabrication import ACTIONS

            if operation in ACTIONS:
                return self.fabrication.action(actor, operation, params, body, idempotency_key)
        if self.external:
            from kleos.api.external import ACTIONS

            if operation in ACTIONS:
                return self.external.action(actor, operation, params, body, idempotency_key)
        if operation == "query_memory" and self.memory:
            return self.memory.search(
                actor, params["projectId"], body.get("query"), limit=body.get("limit", 8)
            )
        if operation.startswith("bambu_") and self.bambu:
            return self.bambu.action(actor, operation, params, body)
        if operation == "upload_artifact" and self.artifacts:
            return self.artifacts.upload(actor, params["projectId"], body, idempotency_key)
        if operation == "upload_reference_image" and self.artifacts:
            return self.artifacts.reference_image(actor, params["projectId"], body)
        if operation == "create_objective":
            request = ObjectiveRequest(
                project_id=params["projectId"],
                objective=body.get("objective"),
                expected_revision=body.get("expectedRevision"),
                idempotency_key=idempotency_key,
            )
            return self.projects.submit(
                actor,
                request.project_id,
                request.objective,
                request.expected_revision,
                request.idempotency_key,
            )
        if operation in {"create_invite", "delete_invite", "change_role", "remove_member", "reset_password"}:
            return self.access.action(actor, operation, params, body)
        raise DomainError(
            "This capability is not available in the replacement yet", "capability_unavailable", 501
        )

    def content(self, actor, operation, params):
        if self.bambu_test_print:
            from kleos.api.bambu_test_print import CONTENTS
            if operation in CONTENTS:
                return self.bambu_test_print.content(actor, operation, params)
        if operation == "release_content" and self.releases:
            return self.releases.content(actor, params["releaseId"])
        if operation == "artifact_content" and self.artifacts:
            return self.artifacts.content(actor, params["artifactId"])
        if operation == "reference_image_content" and self.artifacts:
            return self.artifacts.content(actor, params["imageId"], params["projectId"], reference=True)
        raise DomainError("Artifact delivery is not connected yet", "capability_unavailable", 501)
