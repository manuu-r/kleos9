# Shared project knowledge

`Knowledge` uses real LangMem structured extraction when given a chat model.
The caller supplies public messages; private reasoning and credentials must be
excluded upstream. Each extracted fact references authorized project evidence.
The server binds namespace, revision and advisory authority; memories cannot
approve work or update project requirements. Access is checked before extraction
and again before publication. Production storage is LangGraph AsyncPostgresStore.
Semantic retrieval requires an explicitly configured store index/embedding model;
without one the service returns `recent_project_records` mode. Preferences and
lessons survive later objectives; results retain their recorded revision and
mark source references as current or historical. Historical memories do not
supersede current approved requirements or source files. Retrieval rechecks
project access after the store operation.
