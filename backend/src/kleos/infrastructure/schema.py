"""Explicit model registration for the fresh schema; never imports archived code."""

from importlib import import_module

from kleos.projects.store import Base


def metadata():
    for module in (
        "kleos.projects.store",
        "kleos.projects.graph_repository",
        "kleos.access.service",
        "kleos.artifacts.service",
        "kleos.artifacts.repository_import",
        "kleos.capabilities.fabrication.credentials",
        "kleos.capabilities.fabrication.slicing",
        "kleos.capabilities.external",
        "kleos.capabilities.human_work.evidence",
        "kleos.capabilities.mcp_bridge.runner",
        "kleos.execution.gateway",
        "kleos.execution.capacity",
        "kleos.projects.verification",
        "kleos.orchestration.models",
        "kleos.orchestration.external_waits",
        "kleos.validation.service",
    ):
        import_module(module)
    return Base.metadata
