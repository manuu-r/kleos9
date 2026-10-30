"""Erase terminal project-owned DBOS checkpoints through public SDK APIs only.

Cleanup receipts live in the application database and survive source/artifact erasure.
No workflow input, result, or error is loaded while proving ownership and quiescence.
"""

import hashlib
from datetime import UTC, timedelta

from dbos import DBOSClient
from sqlalchemy import or_, select

from .db import Inbox, Outbox, Project, Run, Source, Task, now

WORKFLOW_NAMES = {"run": "engineering_run", "task": "engineering_task", "index_source": "index_source",
                  "delete_source": "delete_source", "webhook": "process_webhook", "delete_project": "delete_project"}
TERMINAL = {"SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}


def _key(workflow_id):
    return "checkpoint-cleanup:" + hashlib.sha256(workflow_id.encode()).hexdigest()


def _project_for_outbox(session, item):
    body = item.payload
    if item.kind in {"run", "task"}:
        record = session.get(Run if item.kind == "run" else Task, body.get("runId" if item.kind == "run" else "taskId"))
        return record.project_id if record else None
    if item.kind in {"index_source", "delete_source"}:
        source = session.get(Source, body.get("sourceId"))
        return source.project_id if source else None
    if item.kind == "webhook":
        inbox = session.get(Inbox, body.get("inboxId"))
        return inbox.payload.get("projectId") if inbox else None
    return body.get("projectId") if item.kind == "delete_project" else None


class CheckpointRetention:
    def __init__(self, db, settings, client=None):
        self.db, self.settings, self.client = db, settings, client

    @staticmethod
    def schedule(session, project_id, workflow_id, workflow_name, reason, at=None):
        at = at or now()
        key = _key(workflow_id)
        record = session.scalar(select(Outbox).where(Outbox.key == key).with_for_update())
        if record:
            if record.payload["projectId"] != project_id or record.payload["workflowName"] != workflow_name:
                raise RuntimeError("Workflow cleanup ownership does not match its durable receipt")
            # A deletion request takes priority over ordinary retention and rechecks late completions.
            if reason == "project_deletion":
                record.payload = {**record.payload, "reason": reason, "status": "pending"}
            return
        session.add(Outbox(key=key, kind="checkpoint_cleanup", delivered_at=at, payload={
            "projectId": project_id, "workflowId": workflow_id, "workflowName": workflow_name,
            "reason": reason, "status": "pending"}))
        session.flush()

    def capture_project(self, session, project):
        """Capture ownership before source/inbox erasure and suppress undispatched generation."""
        run_ids = list(session.scalars(select(Run.id).where(Run.project_id == project.id)))
        task_ids = list(session.scalars(select(Task.id).where(Task.project_id == project.id)))
        source_ids = list(session.scalars(select(Source.id).where(Source.project_id == project.id)))
        inbox_ids = list(session.scalars(select(Inbox.id).where(Inbox.payload["projectId"].as_string() == project.id)))
        for identifier in run_ids:
            self.schedule(session, project.id, f"run:{identifier}", "engineering_run", "project_deletion")
        for identifier in task_ids:
            self.schedule(session, project.id, f"task:{identifier}", "engineering_task", "project_deletion")
        rows = session.scalars(select(Outbox).where(Outbox.kind.in_(WORKFLOW_NAMES), or_(
            Outbox.payload["runId"].as_string().in_(run_ids), Outbox.payload["taskId"].as_string().in_(task_ids),
            Outbox.payload["sourceId"].as_string().in_(source_ids), Outbox.payload["inboxId"].as_string().in_(inbox_ids),
            Outbox.payload["projectId"].as_string() == project.id)).with_for_update()).all()
        for item in rows:
            self.schedule(session, project.id, item.key, WORKFLOW_NAMES[item.kind], "project_deletion")
            if not item.delivered_at and item.kind != "delete_project":
                item.delivered_at = now()
                item.payload = {**item.payload, "suppressedByProjectDeletion": True}
        self.schedule(session, project.id, f"delete-project:{project.id}", "delete_project", "project_deletion")

    def schedule_expired(self, at, batch_size):
        cutoff = at - timedelta(days=self.settings.transcript_retention_days)
        with self.db.transaction() as session:
            cursor = session.scalar(select(Outbox).where(Outbox.key == "maintenance:checkpoint-scan").with_for_update())
            if not cursor:
                cursor = Outbox(key="maintenance:checkpoint-scan", kind="maintenance_cursor", delivered_at=at, payload={})
                session.add(cursor)
            query = select(Outbox).where(Outbox.kind.in_(WORKFLOW_NAMES),
                                         Outbox.delivered_at < cutoff).order_by(Outbox.id).limit(batch_size)
            if cursor.payload.get("after"):
                query = query.where(Outbox.id > cursor.payload["after"])
            rows = session.scalars(query).all()
            for item in rows:
                project_id = _project_for_outbox(session, item)
                if project_id:
                    self.schedule(session, project_id, item.key, WORKFLOW_NAMES[item.kind], "retention", at)
            cursor.payload = {"after": rows[-1].id if len(rows) == batch_size else None}

    @staticmethod
    def _metadata(client, **filters):
        return client.list_workflows(load_input=False, load_output=False, **filters)

    def _family(self, client, workflow_id, expected_name):
        """Follow recorded parent/fork edges, never workflow-ID prefixes or payload searches."""
        pending, seen, rows = [workflow_id], set(), {}
        while pending:
            identifier = pending.pop()
            if identifier in seen:
                continue
            seen.add(identifier)
            if len(seen) > 1000:
                return None, "workflow_family_limit"
            found = self._metadata(client, workflow_ids=[identifier], limit=2)
            if len(found) > 1:
                return None, "ambiguous_workflow_shard"
            if found:
                record = found[0]
                if record.application_name not in {None, "kleos"} or record.shard_key is not None:
                    return None, "workflow_ownership_mismatch"
                if identifier == workflow_id and record.name != expected_name:
                    return None, "workflow_name_mismatch"
                rows[identifier] = record
            # Descendants may remain after an interrupted or older cleanup removed the root.
            for relation in ("parent_workflow_id", "forked_from"):
                children = self._metadata(client, **{relation: identifier}, limit=1001)
                if len(children) > 1000:
                    return None, "workflow_family_limit"
                for child in children:
                    if getattr(child, relation) != identifier:
                        return None, "workflow_relationship_mismatch"
                    pending.append(child.workflow_id)
        return rows, None

    def _eligible(self, rows, reason, at):
        cutoff_ms = (at - timedelta(days=self.settings.transcript_retention_days)).timestamp() * 1000
        quiescence_ms = max(self.settings.model_timeout_seconds, self.settings.worker_timeout_seconds) * 1000 + 60_000
        for row in rows.values():
            if row.status not in TERMINAL:
                return "workflow_active_or_unknown"
            completed = row.completed_at if row.completed_at is not None else row.updated_at
            if completed is None:
                return "workflow_completion_unproven"
            if reason == "retention" and completed > cutoff_ms:
                return "workflow_retention_not_due"
            # DBOS cancellation can be observed before an in-flight step has unwound.
            if row.status in {"CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"} and (
                    completed > at.timestamp() * 1000 - quiescence_ms):
                return "workflow_cancellation_settling"
        return None

    def _protect_ancestors(self, client, rows, reason, at):
        # A completed child's checkpoint is still needed while an ancestor can replay.
        pending = [row.parent_workflow_id for row in rows.values() if row.parent_workflow_id]
        seen = set(rows)
        while pending:
            identifier = pending.pop()
            if identifier in seen:
                continue
            seen.add(identifier)
            if len(seen) > 1000:
                return "workflow_family_limit"
            parents = self._metadata(client, workflow_ids=[identifier], limit=2)
            if len(parents) > 1:
                return "ambiguous_workflow_shard"
            if not parents:
                continue
            parent = parents[0]
            if parent.application_name not in {None, "kleos"} or parent.shard_key is not None:
                return "workflow_ownership_mismatch"
            blocked = self._eligible({identifier: parent}, reason, at)
            if blocked:
                return "ancestor_" + blocked
            if parent.parent_workflow_id:
                pending.append(parent.parent_workflow_id)
        return None

    def _clean(self, client, payload, at):
        rows, blocked = self._family(client, payload["workflowId"], payload["workflowName"])
        if blocked:
            return {"status": "pending", "reason": blocked}
        blocked = self._eligible(rows, payload["reason"], at)
        if not blocked:
            blocked = self._protect_ancestors(client, rows, payload["reason"], at)
        if blocked:
            return {"status": "pending", "reason": blocked, "workflowCount": len(rows)}
        # Recheck the entire graph immediately before deletion. Never recursively delete unchecked children.
        current, blocked = self._family(client, payload["workflowId"], payload["workflowName"])
        if blocked or set(current) != set(rows):
            return {"status": "pending", "reason": blocked or "workflow_family_changed"}
        blocked = self._eligible(current, payload["reason"], at)
        if not blocked:
            blocked = self._protect_ancestors(client, current, payload["reason"], at)
        if blocked:
            return {"status": "pending", "reason": blocked}
        if current:
            client.delete_workflows(list(current), delete_children=False)
        remaining, blocked = self._family(client, payload["workflowId"], payload["workflowName"])
        if blocked or remaining:
            return {"status": "pending", "reason": blocked or "workflow_cleanup_unconfirmed"}
        return {"status": "complete", "workflowCount": len(current)}

    def sweep(self, project_id=None, at=None, batch_size=250):
        at = at or now()
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        with self.db.transaction() as session:
            query = select(Outbox).where(Outbox.kind == "checkpoint_cleanup",
                                         Outbox.payload["status"].as_string() != "complete")
            if project_id:
                query = query.where(Outbox.payload["projectId"].as_string() == project_id)
            records = [(r.id, dict(r.payload)) for r in session.scalars(query.order_by(
                Outbox.payload["checkedAt"].as_string().asc().nullsfirst(), Outbox.id).limit(batch_size))]
        counts, client, owned = {"checkpointCleanups": 0, "checkpointDeferred": 0}, self.client, False
        try:
            if records and client is None:
                client = DBOSClient(system_database_url=self.settings.dbos_database_url, application_name="kleos")
                owned = True
            for record_id, payload in records:
                try:
                    result = self._clean(client, payload, at)
                except Exception as error:
                    result = {"status": "pending", "reason": "workflow_cleanup_unavailable", "errorType": type(error).__name__}
                with self.db.transaction() as session:
                    record = session.get(Outbox, record_id)
                    record.payload = {**record.payload, "status": result["status"], "checkedAt": at.isoformat(),
                                      "result": result}
                counts["checkpointCleanups" if result["status"] == "complete" else "checkpointDeferred"] += 1
        except Exception:
            counts["checkpointDeferred"] += len(records)
        finally:
            if owned:
                client.destroy()
        self._finish_deletions({payload["projectId"] for _, payload in records} | ({project_id} if project_id else set()), at)
        return counts

    def _finish_deletions(self, project_ids, at):
        with self.db.transaction() as session:
            for identifier in project_ids:
                project = session.scalar(select(Project).where(Project.id == identifier).with_for_update())
                if not project or not project.deleted_at or not project.state.get("deletion", {}).get("applicationPurged"):
                    continue
                pending = session.scalar(select(Outbox.id).where(Outbox.kind == "checkpoint_cleanup",
                    Outbox.payload["projectId"].as_string() == identifier,
                    Outbox.payload["status"].as_string() != "complete").limit(1))
                if not pending:
                    project.state = {"deletion": {**project.state["deletion"], "status": "purged",
                                                   "dbosCheckpointsPurgedAt": at.isoformat(), "purgedAt": at.isoformat()}}
