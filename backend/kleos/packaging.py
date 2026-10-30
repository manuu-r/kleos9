"""Reproducible release downloads and geometry-derived placement references."""
import io
import json
import zipfile
from pathlib import PurePosixPath

from sqlalchemy import select

from .contracts import DomainError
from .db import Artifact, Release
from .domain import get_project
from .storage import digest


def assembly_reference(manifest):
    geometry = manifest.get("interfaces", {}).get("mechanical", {})
    components = geometry.get("components", [])
    return {
        "title": "Assembly placement reference", "units": "mm",
        "coordinateFrame": "minimum corner of the internal enclosure",
        "internalEnvelope": geometry.get("internalMm"),
        "placements": [{"componentId": c.get("id"), "sizeMm": c.get("sizeMm"),
                        "minimumCornerMm": c.get("positionMm")} for c in components],
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
                                                          Artifact.tenant_id == principal.tenant_id)).all()
            if len(rows) != len(refs) or sum(a.size for a in rows) > 100 * 1024 * 1024:
                raise DomainError("Release closure is unavailable or exceeds the 100 MiB download limit", 409)
            manifest = {"releaseId": release.id, "projectId": release.project_id,
                        "engineeringRevision": release.revision, "mode": release.mode,
                        **release.manifest}
        output = io.BytesIO()
        names = set()
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
                content = self.store.get(artifact.key)
                if digest(content) != refs[artifact.id]:
                    raise DomainError("Release artifact integrity check failed", 409)
                write(f"{artifact.lane}/{artifact.name}", content)
            write("release-manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode())
            write("assembly-placement-reference.json", json.dumps(assembly_reference(manifest), indent=2).encode())
        return output.getvalue()
