# Installed capabilities

This registry contains operator-owned tool installations and immutable versions,
not a list of names that automatically imply functionality. Empty registry means
no available capabilities. `available()` reports only installed enabled tools.
Each binding includes its provider/version/installation hash, argument schema,
qualification flag, effect class, description and bounded skill instructions.
Agent/client requests cannot supply executables, endpoints, credentials or their
own qualification claims. Registration is startup configuration, not a public API.

An attempt pins a registry snapshot. Digest mismatches fail closed; operator
revocation fences even previously pinned bindings. `tools(snapshot)` supplies
ModelSpecialist RegisteredTools with typed arguments and a mandatory host
permission check before every invocation. Providers never grant approval.
Non-validation tool output cannot mark checks independently validated.

`native.register_native` binds only fixed read/patch/build/analyze/render/finish
operations to an isolated execution gateway. Files remain scoped by TaskSpec and
the gateway's authoritative artifact service. actionId is stable for one call;
a later build after a patch must use a new actionId. Shell commands and arbitrary
flags are not valid tool parameters.

`services.register_service` binds explicitly implemented research, supplier and
human-work operations to a qualified server-owned gateway. A gateway must enforce
ownership of observed external jobs and evidence artifacts, provider credentials,
approval/amount/scope checks, durable external receipts and uncertain-effect
reconciliation. No search/draft result fabricates hiring or purchases. Purchase
and hire remain unavailable unless an actual qualified gateway is installed and
the server's physical-action policy permits the exact approved action.

MCP/MM plugins can implement these boundaries when deployed in isolated workers
or qualified backend services. A desktop plugin visible in a chat is not itself
a server installation. Plugin prose supplies expertise, not authority or a way
around the fixed tool host. No default fake integrations are installed.
