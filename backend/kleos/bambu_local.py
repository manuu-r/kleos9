"""User-invoked local preparation client. No print-start, MQTT control or cloud bypass."""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .bambu_contracts import MAX_PACKAGE_BYTES, BambuProfile
from .bambu_slicing import PROFILE_FILES, inspect_package, load_profile, mesh_bounds


def connect_uri(path):
    if path.is_symlink():
        raise ValueError("Do not hand off symlinked packages")
    path = path.resolve()
    if path.is_symlink() or not path.is_file() or path.suffix != ".3mf" or any(c in str(path) for c in "&?#\r\n"):
        raise ValueError("Use a regular local 3MF path without URL delimiters")
    # Format from Bambu's published Orca integration example (nested import payload).
    payload = "?version=v1.0.0&path=" + path.as_posix() + "&name=" + path.stem
    return "bambu-connect://import-file?path=" + quote(payload, safe="")


def docker_slice(source, profiles, profile, *, desktop_compatibility=False, runner=subprocess.run):
    policy = BambuProfile.model_validate(profile)
    if not policy.qualified:
        raise ValueError("Unqualified slicing profile")
    profiles = profiles.resolve()
    manifest = load_profile(profiles, policy.bundle_sha256)
    if (manifest["printerModelId"] != policy.printer_model_id or manifest["nozzleMm"] != policy.nozzle_mm
            or manifest["material"] != policy.material):
        raise ValueError("Printer/nozzle/material does not match the requested profile")
    mesh_bounds(source)
    with tempfile.TemporaryDirectory(prefix="kleos-bambu-local-") as tmp:
        root = Path(tmp)
        mesh = root / "input.stl"
        mesh.write_bytes(source)
        mesh.chmod(0o444)
        staged_profiles = root / "profiles"
        staged_profiles.mkdir(mode=0o755)
        for filename in ("manifest.json", *PROFILE_FILES):
            (staged_profiles / filename).write_bytes((profiles / filename).read_bytes())
            (staged_profiles / filename).chmod(0o444)
        name = "kleos-bambu-" + uuid.uuid4().hex
        args = ["docker", "run", "--name", name, "--rm", "--pull=never", "--platform", "linux/amd64", "--network", "none",
                "--read-only", "--cpus", "2", "--memory", "4g", "--pids-limit", "128",
                "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETUID", "--cap-add", "SETGID",
                "--cap-add", "KILL", "--cap-add", "DAC_OVERRIDE",
                "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,size=1024m",
                "--mount", f"type=bind,src={mesh},dst=/input/input.stl,readonly",
                "--mount", f"type=bind,src={staged_profiles},dst=/profiles,readonly"]
        if desktop_compatibility:
            # Existing Linux guard verifies network-none before permitting this local-only fallback.
            args += ["--env", "KLEOS_NATIVE_DOCKER_NETWORK_NONE=1"]
        args += [policy.image, policy.bundle_sha256]
        # Bounded disk logs/output; never pipe unbounded slicer text into RAM or display it.
        with tempfile.TemporaryFile() as output:
            try:
                result = runner(args, stdout=output, stderr=output, stdin=subprocess.DEVNULL, timeout=330, check=False)
            except subprocess.TimeoutExpired:
                runner(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
                raise
            if result.returncode:
                raise RuntimeError("Isolated slicing failed; no print was sent")
            if output.tell() > 24 * 1024 * 1024:
                raise ValueError("Slicing result exceeds its transfer ceiling")
            output.seek(0)
            data = json.loads(output.read(24 * 1024 * 1024))
        if data["schemaVersion"] != "bambu-slice-v1" or data["measurements"]["sourceSha256"] != hashlib.sha256(source).hexdigest():
            raise ValueError("Slicing result does not match the accepted mesh")
        if data["measurements"]["bundleSha256"] != policy.bundle_sha256:
            raise ValueError("Slicing result profile mismatch")
        package = base64.b64decode(data["packageBase64"], validate=True)
        inspect_package(package)
        return package


def prepare(client, project_id, ident, profiles, destination, desktop_compatibility=False):
    if not re.fullmatch(r"[A-Za-z0-9-]{1,36}", project_id) or not re.fullmatch(r"[A-Za-z0-9-]{1,36}", ident):
        raise ValueError("Invalid project/preparation identity")
    path = f"/projects/{project_id}/print-preparations/{ident}"
    response = client.get(path)
    response.raise_for_status()
    ticket = response.json()
    if ticket["status"] == "ready_for_operator":
        if not re.fullmatch(r"[A-Za-z0-9-]{1,36}", ticket["preparedArtifactId"]):
            raise ValueError("Invalid prepared artifact identity")
        with client.stream("GET", f'/artifacts/{ticket["preparedArtifactId"]}/content') as response:
            response.raise_for_status()
            package = bytearray()
            for chunk in response.iter_bytes():
                package.extend(chunk)
                if len(package) > MAX_PACKAGE_BYTES:
                    raise ValueError("Prepared package exceeds its transfer ceiling")
        if hashlib.sha256(package).hexdigest() != ticket["preparedSha256"]:
            raise ValueError("Prepared package hash mismatch")
        inspect_package(package)
        if destination.exists():
            if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != ticket["preparedSha256"]:
                raise ValueError("Existing local output differs; choose a new path")
        else:
            with destination.open("xb") as file:
                file.write(package)
        return ticket
    if ticket["status"] != "awaiting_local_slicing":
        raise ValueError("Preparation is not awaiting slicing; inspect its current state")
    artifact = ticket["sourceArtifactId"]
    if not re.fullmatch(r"[A-Za-z0-9-]{1,36}", artifact):
        raise ValueError("Invalid source artifact identity")
    with client.stream("GET", f"/artifacts/{artifact}/content") as response:
        response.raise_for_status()
        source = bytearray()
        for chunk in response.iter_bytes():
            source.extend(chunk)
            if len(source) > MAX_PACKAGE_BYTES:
                raise ValueError("Source exceeds the slicing input ceiling")
    if hashlib.sha256(source).hexdigest() != ticket["sourceSha256"]:
        raise ValueError("Accepted source hash mismatch")
    package = docker_slice(bytes(source), profiles, ticket["profile"], desktop_compatibility=desktop_compatibility)
    checksum = hashlib.sha256(package).hexdigest()
    upload = client.post(f"/projects/{project_id}/artifacts", json={
        "expectedEngineeringRevision": ticket["engineeringRevision"], "sha256": checksum,
        "file": {"path": f"bambu-{ident}.gcode.3mf", "mediaType": "application/zip", "encoding": "base64",
                 "content": base64.b64encode(package).decode()}})
    upload.raise_for_status()
    completed = client.post(path + "/complete", json={"artifactId": upload.json()["id"], "sha256": checksum,
                                                    "bundleSha256": ticket["profile"]["bundleSha256"]})
    completed.raise_for_status()
    # The server rechecked release/revision/cancellation before this local handoff is made available.
    with destination.open("xb") as file:
        file.write(package)
    return completed.json()


def main():
    parser = argparse.ArgumentParser(description="Slice an accepted Kleos part for operator review in Bambu Connect")
    parser.add_argument("--platform", required=True, help="HTTPS API base, ending /api/v1/")
    parser.add_argument("--project", required=True)
    parser.add_argument("--preparation", required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--open-connect", action="store_true", help="Open Bambu Connect for manual review; does not press Print")
    parser.add_argument("--desktop-compatibility", action="store_true")
    args = parser.parse_args()
    parsed = urlsplit(args.platform)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/api/v1"):
        raise ValueError("Use the authenticated HTTPS Kleos API origin")
    # No provider or printer credentials are passed to the offline native image.
    with httpx.Client(base_url=args.platform.rstrip("/"), timeout=30, follow_redirects=False, trust_env=False,
                      headers={"Authorization": "Bearer " + os.environ["KLEOS_PLATFORM_TOKEN"],
                               "X-Organization-Id": os.environ["KLEOS_ORGANIZATION_ID"]}) as client:
        result = prepare(client, args.project, args.preparation, args.profiles, args.output, args.desktop_compatibility)
    print(json.dumps({"status": result["status"], "sha256": result["preparedSha256"], "physicalAcceptance": False}))
    if args.open_connect:
        import webbrowser
        if not webbrowser.open(connect_uri(args.output)):
            raise RuntimeError("Bambu Connect did not open; import the prepared file manually")


if __name__ == "__main__":
    main()
