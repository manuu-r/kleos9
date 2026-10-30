"""Real DBOS checkpoint deletion, using disposable databases on the opt-in local server."""

import os
from uuid import uuid4

import pytest
from dbos import DBOS, DBOSClient, SetWorkflowID
from test_postgres import postgres as postgres

from kleos.checkpoint_retention import CheckpointRetention
from kleos.db import Project

pytestmark = pytest.mark.skipif(not os.getenv("KLEOS_TEST_POSTGRES_URL"), reason="PostgreSQL opt-in required")


@DBOS.step(name="runtime_checkpoint_step")
def _checkpoint_step():
    return {"context": "Synthetic private context for isolated checkpoint erasure verification"}


@DBOS.workflow(name="runtime_checkpoint_child")
def _checkpoint_child():
    return _checkpoint_step()


@DBOS.workflow(name="runtime_checkpoint_parent")
def _checkpoint_parent():
    return _checkpoint_child()


def test_real_dbos_terminal_family_checkpoint_erasure(postgres):
    with postgres.db.transaction() as session:
        session.add(Project(id="project", tenant_id="org", owner_id="test", name="Fixture", brief="Fixture"))
    DBOS(config={"name": "kleos", "system_database_url": postgres.settings.dbos_database_url,
                 "application_version": "checkpoint-retention-verification"})
    client = None
    try:
        DBOS.launch()
        identifier = "runtime-checkpoint-" + uuid4().hex
        with SetWorkflowID(identifier):
            result = _checkpoint_parent()
        assert result["context"].startswith("Synthetic private context")
        client = DBOSClient(system_database_url=postgres.settings.dbos_database_url, application_name="kleos")
        children = client.list_workflows(parent_workflow_id=identifier, load_input=False, load_output=False)
        assert len(children) == 1
        assert client.list_workflow_steps(children[0].workflow_id, load_output=False)
        with postgres.db.transaction() as session:
            CheckpointRetention.schedule(session, "project", identifier, "runtime_checkpoint_parent", "project_deletion")
        cleaner = CheckpointRetention(postgres.db, postgres.settings, client)
        assert cleaner.sweep() == {"checkpointCleanups": 1, "checkpointDeferred": 0}
        assert client.list_workflows(workflow_ids=[identifier, children[0].workflow_id],
                                     load_input=False, load_output=False) == []
        assert client.list_workflow_steps(children[0].workflow_id, load_output=False) == []
    finally:
        if client:
            client.destroy()
        DBOS.destroy()
