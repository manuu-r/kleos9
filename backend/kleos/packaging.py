"""Reproducible release downloads and geometry-derived placement references."""
import io
import json
import zipfile
from pathlib import PurePosixPath

from sqlalchemy import select

from .cad_parts import parse_assembly_manifest, validate_manifest_closure
from .contracts import DomainError
from .db import Artifact, Release
from .design_intent import derive_intent
from .domain import get_project
from .storage import digest


def assembly_reference(manifest, cad_assemblies=None):
    geometry = manifest.get("interfaces", {}).get("mechanical", {})
    components = geometry.get("components", [])
    return {
        "title": "Assembly placement reference", "units": "mm",
        "coordinateFrame": "minimum corner of the internal enclosure",
        "internalEnvelope": geometry.get("internalMm"),
        "placements": [{"componentId": c.get("id"), "sizeMm": c.get("sizeMm"),
                        "minimumCornerMm": c.get("positionMm")} for c in components],
        "cadAssemblies": cad_assemblies or [],
        "limitations": ["Placement coordinates are derived from the accepted interface geometry.",
                        "Insertion order, clearances during insertion, fasteners, wiring and torque require review.",
                        "Physical assembly and acceptance are simulated in this release."],
    }


class ReleasePackaging:
    def __init__(self, db, store):
        self.db, self.store = db, store

    def bundle(self, principal, release_id):
        with self.db.transaction() as session:
            release = session.scalar(select(Release).where(Release.id == release_id,
                                                           Release.tenant_id == principal.tenant_id))
            if not release:
                raise DomainError("Release not found", 404)
            get_project(session, release.project_id, principal)
            refs = release.manifest["artifacts"]
            rows = session.scalars(select(Artifact).where(Artifact.id.in_(refs),
                Artifact.tenant_id == principal.tenant_id, Artifact.project_id == release.project_id,
                Artifact.revision == release.revision)).all()
            if len(rows) != len(refs) or sum(a.size for a in rows) > 100 * 1024 * 1024:
                raise DomainError("Release closure is unavailable or exceeds the 100 MiB download limit", 409)
            manifest = {"releaseId": release.id, "projectId": release.project_id,
                        "engineeringRevision": release.revision, "mode": release.mode,
                        **release.manifest}
        output = io.BytesIO()
        names = set()
        contents = {}
        for artifact in rows:
            content = self.store.get(artifact.key)
            if digest(content) != refs[artifact.id]:
                raise DomainError("Release artifact integrity check failed", 409)
            contents[(artifact.lane, artifact.name)] = content
        cad_assemblies = []
        approved = manifest.get('approvedDesign')
        design_intent = manifest.get('requirements', {}).get('designIntent')
        design_reference = None
        design_reference_bytes = None
        if approved is not None or design_intent is not None:
            try:
                if not isinstance(approved, dict) or derive_intent(approved) != design_intent:
                    raise ValueError('Approved release appearance reference is inconsistent')
                design_reference_bytes = json.dumps(approved, indent=2, sort_keys=True, allow_nan=False).encode()
                design_reference = {'path': 'approved-design-reference.json',
                    'sha256': digest(design_reference_bytes), 'sceneHash': approved['sceneHash'],
                    'approvalVersion': approved['version'], 'sceneVersion': approved['sceneVersion'],
                    'authority': 'user_approved_appearance', 'units': 'relative',
                    'dimensionAuthority': 'accepted_engineering_interfaces_and_CAD_mm'}
            except (DomainError, ValueError, TypeError, KeyError) as exc:
                raise DomainError('Accepted design reference is missing or no longer matches its engineering contract', 409) from exc
        for artifact in sorted(rows, key=lambda item: item.name):
            if artifact.lane != "mechanics" or not artifact.name.endswith("assembly-manifest.json"):
                continue
            try:
                cad = parse_assembly_manifest(contents[(artifact.lane, artifact.name)])
                validate_manifest_closure(cad, {name: content for (lane, name), content in contents.items() if lane == "mechanics"})
            except (ValueError, TypeError, KeyError) as exc:
                raise DomainError("Accepted CAD manufacturing part closure is incomplete or changed", 409) from exc
            cad_assemblies.append({"manifestArtifactId": artifact.id, "manifestSha256": artifact.sha256,
                "path": f"mechanics/{artifact.name}", "units": cad["units"], "coordinateFrame": cad["coordinateFrame"],
                "separation": cad["separation"], "assembly": cad["assembly"], "parts": cad["parts"],
                "archivePathPrefix": "mechanics/", "productionAuthorized": False,
                "printability": "UNKNOWN", "materialQualification": "UNKNOWN"})
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def write(name, content):
                if name in names:
                    raise DomainError("Release contains conflicting file names", 409)
                names.add(name)
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
            for artifact in sorted(rows, key=lambda a: (a.lane, a.name, a.id)):
                path = PurePosixPath(artifact.name)
                if path.is_absolute() or ".." in path.parts or "\\" in artifact.name:
                    raise DomainError("Release contains an invalid file path", 409)
                content = contents[(artifact.lane, artifact.name)]
                write(f"{artifact.lane}/{artifact.name}", content)
            write("release-manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode())
            if design_reference_bytes is not None:
                write('approved-design-reference.json', design_reference_bytes)
            write("assembly-placement-reference.json", json.dumps(assembly_reference(manifest, cad_assemblies), indent=2).encode())
            write("manufacturing-manifest.json", json.dumps({"schemaVersion": "manufacturing-manifest-v1",
                "releaseId": release.id, "engineeringRevision": release.revision, "releaseMode": release.mode,
                "assemblies": cad_assemblies, "productionAuthorized": False, "physicalStagesSimulated": True,
                **({'designReference': design_reference} if design_reference is not None else {}),
                "limitations": ["Exploded translations are display-only; manufacturing files retain assembled millimetre coordinates.",
                                "Slicing, material suitability, fabrication, packing, delivery and physical acceptance require their own approvals."]},
                indent=2, sort_keys=True).encode())
        return output.getvalue()
