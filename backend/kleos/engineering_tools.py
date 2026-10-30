"""Versioned engineering adapters, independent of model choice and release authority.

Operators install and qualify worker images. Project writers select those tools;
they cannot introduce container images, host commands, secrets or validators.
"""
import hashlib
import json
import re
from copy import deepcopy

from pydantic import ValidationError

from .contracts import DomainError, EngineeringToolChoice, EngineeringToolDefinition

DEFAULT_TOOL_POLICY = {
    "mechanics": {"id": "build123d", "version": "1"},
    "electronics": {"id": "atopile-kicad", "version": "1"},
}

BUILTINS = [
    EngineeringToolDefinition(
        id="build123d", version="1", name="build123d", lane="mechanics", runner="build123d",
        sourceContract="Produce enclosure.py defining a build123d Shape named assembly, in millimetres. "
        "The STEP assembly contains enclosure solids only; do not add component visualization solids. "
        "The runner exports outputs/model.step and outputs/model.stl for independent geometry inspection.",
        outputFiles=["outputs/model.step", "outputs/model.stl"], qualified=True,
        qualificationReference="builtin:build123d-contract-v1",
        limitations=["Each generated enclosure still requires independent geometry and interface checks."]),
    EngineeringToolDefinition(
        id="atopile-kicad", version="1", name="atopile / KiCad", lane="electronics", runner="atopile-kicad",
        sourceContract="Produce an atopile project with ato.yaml and its source files. The build must export "
        "one KiCad PCB and one KiCad schematic. The runner executes atopile, KiCad ERC/DRC and Gerber/drill exports. "
        "State unsupported layout, symbol/footprint and circuit correspondence assumptions explicitly.",
        outputFiles=["*.kicad_pcb", "*.kicad_sch", "outputs/gerbers/*"], qualified=True,
        qualificationReference="builtin:atopile-kicad-contract-v1",
        limitations=["PCB layout and circuit correspondence remain design-specific qualification gates."]),
]


def tool_policy(project):
    return deepcopy(project.state.get("toolPolicy", DEFAULT_TOOL_POLICY))


def profile_hash(profile):
    return hashlib.sha256(json.dumps(profile.model_dump(mode="json", by_alias=True),
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EngineeringToolRegistry:
    def __init__(self, settings):
        self.settings = settings
        self.profiles = {(profile.id, profile.version): profile
                         for profile in [*BUILTINS, *settings.engineering_tools]}

    @staticmethod
    def image_key(profile):
        if profile.id in {"build123d", "atopile-kicad"}:
            return profile.lane
        return f"{profile.id}@{profile.version}"

    def configured(self, profile):
        images = (self.settings.docker_native_images if self.settings.execution_backend == "docker"
                  else self.settings.ecs_task_definitions if self.settings.execution_backend == "ecs" else {})
        reference = images.get(self.image_key(profile), "")
        if profile.id not in {"build123d", "atopile-kicad"}:
            # A qualification applies to an immutable installation, not a tag
            # that can silently point at another worker tomorrow.
            pattern = r"(?:.+@)?sha256:[a-f0-9]{64}" if self.settings.execution_backend == "docker" else r".+:[1-9][0-9]*"
            return bool(re.fullmatch(pattern, reference))
        return bool(reference)

    def installation_hash(self, profile):
        images = (self.settings.docker_native_images if self.settings.execution_backend == "docker"
                  else self.settings.ecs_task_definitions)
        return hashlib.sha256(json.dumps({"backend": self.settings.execution_backend,
            "reference": images.get(self.image_key(profile))}, sort_keys=True).encode()).hexdigest()

    def get(self, choice, tenant_id, selectable=True):
        try:
            choice = EngineeringToolChoice.model_validate(choice)
        except ValidationError as exc:
            raise DomainError("Choose a registered engineering tool and version", 422, "invalid_tool") from exc
        profile = self.profiles.get((choice.id, choice.version))
        if not profile or (profile.tenant_ids and tenant_id not in profile.tenant_ids):
            raise DomainError("Engineering tool is not available to this organization", 404, "tool_unavailable")
        if selectable and not profile.qualified:
            raise DomainError("This engineering tool has not been operationally qualified", 409, "tool_unqualified")
        if selectable and profile.id not in {"build123d", "atopile-kicad"} and not self.configured(profile):
            raise DomainError("The engineering tool worker is not installed", 409, "tool_unconfigured")
        return profile

    def validate_policy(self, policy, tenant_id):
        if not isinstance(policy, dict) or set(policy) != set(DEFAULT_TOOL_POLICY):
            raise DomainError("Choose CAD and PCB tools", 422, "invalid_tool_policy")
        result = {}
        for lane, choice in policy.items():
            profile = self.get(choice, tenant_id)
            if profile.lane != lane:
                raise DomainError("The selected tool does not support this engineering lane", 422, "invalid_tool_lane")
            result[lane] = {"id": profile.id, "version": profile.version}
        return result

    def snapshot(self, project, lane):
        if lane not in DEFAULT_TOOL_POLICY:
            return None
        choice = tool_policy(project)[lane]
        profile = self.get(choice, project.tenant_id)
        return self.describe(profile, project.state.get("toolPolicyVersion", 0))

    def describe(self, profile, policy_version=0):
        return {**profile.model_dump(mode="json", by_alias=True, exclude={"tenant_ids"}),
                "definitionHash": profile_hash(profile), "imageKey": self.image_key(profile),
                "installationHash": self.installation_hash(profile),
                "policyVersion": policy_version}

    def pinned(self, snapshot, tenant_id, lane):
        choice = {key: snapshot[key] for key in ("id", "version")} if snapshot else DEFAULT_TOOL_POLICY[lane]
        profile = self.get(choice, tenant_id)
        if profile.lane != lane or (snapshot and snapshot.get("definitionHash") != profile_hash(profile)):
            raise DomainError("Pinned engineering tool changed; restore its version or start a new attempt", 409, "tool_changed")
        if snapshot and profile.id not in {"build123d", "atopile-kicad"} and (
                snapshot.get("installationHash") != self.installation_hash(profile)):
            raise DomainError("The qualified plugin installation changed; register a new version", 409, "tool_changed")
        return profile

    def catalog(self, tenant_id):
        tools = []
        for profile in self.profiles.values():
            if profile.tenant_ids and tenant_id not in profile.tenant_ids:
                continue
            tools.append({**profile.model_dump(mode="json", by_alias=True, exclude={"tenant_ids"}),
                          "configured": self.configured(profile),
                          "isDefault": DEFAULT_TOOL_POLICY[profile.lane] == {"id": profile.id, "version": profile.version}})
        return {"tools": tools, "defaultPolicy": deepcopy(DEFAULT_TOOL_POLICY)}
