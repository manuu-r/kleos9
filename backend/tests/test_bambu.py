import base64
import io
import json
import struct
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from test_control_plane import create

from kleos.auth import Principal
from kleos.bambu_contracts import BambuProfile, model_catalog
from kleos.bambu_local import connect_uri, docker_slice, prepare
from kleos.bambu_print import BambuPrintService
from kleos.bambu_profiles import build_bundle, resolve_preset
from kleos.bambu_slicing import inspect_package, load_profile, mesh_bounds, slice_mesh
from kleos.bambu_telemetry import BambuPrinter, _packet, _read, _string, identify_model, observation
from kleos.contracts import DomainError
from kleos.db import Artifact, Project, Release, Validation, uid
from kleos.storage import digest


def cube(size=10):
    # A bounded binary mesh with nonzero bounds (fixture, not printability evidence).
    triangle = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, size, 0, 0, 0, size, size, 0)
    return b"\x00" * 80 + struct.pack("<I", 1) + triangle


def package(entries=None):
    file = io.BytesIO()
    with zipfile.ZipFile(file, "w") as archive:
        for name, content in (
            entries
            or {
                "Metadata/plate_1.gcode": "; fixture only\nG1 X1 Y1 E1\n",
                "Metadata/slice_info.config": '<config><metadata key="prediction" value="42"/></config>',
            }
        ).items():
            archive.writestr(name, content)
    return file.getvalue()


@pytest.fixture
def profile_bundle(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    configs = {
        "machine.json": {
            "printer_model": "Bambu Lab P1S",
            "nozzle_diameter": ["0.4"],
            "printable_area": ["0x0", "256x0", "256x256", "0x256"],
            "printable_height": "256",
        },
        "process.json": {"layer_height": "0.2"},
        "filament.json": {"filament_type": ["PLA"]},
    }
    for name, value in configs.items():
        (root / name).write_text(json.dumps(value))
    manifest = {
        "studioVersion": "2.8.2.61",
        "printerModelId": "C12",
        "nozzleMm": 0.4,
        "material": "PLA",
        "buildVolumeMm": [256, 256, 256],
        "files": {name: digest((root / name).read_bytes()) for name in configs},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    policy = BambuProfile(
        printerModelId="C12",
        nozzleMm=0.4,
        material="PLA",
        bundleSha256=digest((root / "manifest.json").read_bytes()),
        image="fixture/slicer@sha256:" + "f" * 64,
        qualified=True,
    )
    return root, policy


def accepted_source(platform, policy):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        source = Artifact(
            id=uid(),
            tenant_id="org-a",
            project_id=ident,
            revision=1,
            lane="mechanics",
            name="case.stl",
            key="fixture/" + ident,
            sha256=digest(cube()),
            size=len(cube()),
            status="accepted",
        )
        session.add(source)
        platform.store.put(source.key, cube())
        release = Release(
            id=uid(),
            tenant_id="org-a",
            project_id=ident,
            revision=1,
            mode="digital",
            approved_by="fixture",
            manifest={
                "artifacts": {source.id: source.sha256},
                "requirements": project.requirements,
                "interfaces": project.interfaces,
            },
        )
        session.add(release)
        project.accepted_release_id = release.id
        platform.settings.bambu_slice_profiles = {"p1s-pla": policy}
        return ident, {
            "releaseId": release.id,
            "artifactId": source.id,
            "profileId": "p1s-pla",
            "expectedEngineeringRevision": 1,
        }


def start_job(platform, body, ident, key="test-preparation"):
    response = platform.client.post(
        f"/api/v1/projects/{ident}/print-preparations", json=body, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 200, response.text
    return response.json()


def uploaded_package(platform, ident, policy, raw=None):
    raw = package() if raw is None else raw
    response = platform.client.post(
        f"/api/v1/projects/{ident}/artifacts",
        json={
            "file": {
                "path": "fixture.gcode.3mf",
                "content": base64.b64encode(raw).decode(),
                "encoding": "base64",
                "mediaType": "application/zip",
            },
            "sha256": digest(raw),
            "expectedEngineeringRevision": 1,
        },
    )
    assert response.status_code == 201
    return {"artifactId": response.json()["id"], "sha256": digest(raw), "bundleSha256": policy.bundle_sha256}


def test_all_pinned_models_have_ids_and_nozzle_choices():
    models = model_catalog()["models"]
    assert len(models) == 14 and len({m["id"] for m in models}) == 14
    for model in models:
        assert identify_model(model["id"]) == model["id"]
        assert identify_model(model["name"]) == model["id"]
        assert model["nozzlesMm"] and len(model["sourceSha256"]) == 64


def test_preparation_upload_and_download_are_real_data_not_physical_acceptance(platform, profile_bundle):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    job = start_job(platform, body, ident)
    assert start_job(platform, body, ident)["id"] == job["id"]
    posted = uploaded_package(platform, ident, policy)
    path = f"/api/v1/projects/{ident}/print-preparations/{job['id']}/complete"
    response = platform.client.post(path, json=posted)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ready_for_operator" and result["physicalAcceptance"] is False
    assert result["measurements"]["estimatedSeconds"] == 42
    assert platform.client.post(path, json=posted).json() == result
    downloaded = platform.client.get(f"/api/v1/artifacts/{result['preparedArtifactId']}/content")
    assert digest(downloaded.content) == result["preparedSha256"]
    with platform.db.transaction() as session:
        artifacts = session.scalars(select(Artifact).where(Artifact.status == "prepared")).all()
        assert len(artifacts) == 1 and artifacts[0].provenance["authority"] == "local_adapter_report"
        assert session.scalar(select(Validation)) is None
    assert (
        platform.client.get(f"/api/v1/projects/{ident}/print-preparations").json()["preparations"][0][
            "status"
        ]
        == "ready_for_operator"
    )


@pytest.mark.parametrize("change", ["revision", "contract", "release", "source"])
def test_stale_print_input_is_fenced(platform, profile_bundle, change):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    job = start_job(platform, body, ident)
    posted = uploaded_package(platform, ident, policy)
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        if change == "revision":
            project.engineering_revision += 1
        elif change == "contract":
            project.interfaces = {"changed": True}
        elif change == "release":
            project.accepted_release_id = None
        else:
            session.get(Artifact, body["artifactId"]).sha256 = "e" * 64
    result = platform.client.post(
        f"/api/v1/projects/{ident}/print-preparations/{job['id']}/complete", json=posted
    )
    assert result.status_code in {404, 409}
    assert platform.client.get(f"/api/v1/projects/{ident}/print-preparations").json()["preparations"] == []


def test_cancellation_blocks_publication_and_tenant_scope(platform, profile_bundle):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    job = start_job(platform, body, ident)
    posted = uploaded_package(platform, ident, policy)
    service = BambuPrintService(platform.db, platform.settings, platform.store)
    with pytest.raises(DomainError):
        service.read(Principal("other", "org-b", "owner"), ident, job["id"])
    path = f"/api/v1/projects/{ident}/print-preparations/{job['id']}"
    assert platform.client.delete(path).json()["status"] == "cancelled"
    assert platform.client.post(path + "/complete", json=posted).status_code == 409


def test_unqualified_profile_or_candidate_mesh_cannot_prepare(platform, profile_bundle):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    platform.settings.bambu_slice_profiles = {}
    assert (
        platform.client.post(
            f"/api/v1/projects/{ident}/print-preparations", json=body, headers={"Idempotency-Key": "first"}
        ).status_code
        == 503
    )
    platform.settings.bambu_slice_profiles = {"p1s-pla": policy}
    with platform.db.transaction() as session:
        session.get(Artifact, body["artifactId"]).status = "candidate"
    assert (
        platform.client.post(
            f"/api/v1/projects/{ident}/print-preparations", json=body, headers={"Idempotency-Key": "second"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "entries",
    [
        {"file.stl": "unsliced"},
        {"../plate.gcode": "G1 E1"},
        {"Metadata/plate_1.gcode": "G1 E1", "Metadata/plate_2.gcode": "G1 E1"},
        {"Metadata/plate_1.gcode": "\x00"},
        {"Metadata/plate_1.gcode": "G1 E1", "Metadata/slice_info.config": "<!DOCTYPE a><config/>"},
    ],
)
def test_unsafe_or_unsliced_packages_rejected(entries):
    with pytest.raises(ValueError):
        inspect_package(package(entries))


def test_profile_checks_and_fixed_slicing_args(tmp_path, profile_bundle):
    root, policy = profile_bundle
    (tmp_path / "input.stl").write_bytes(cube())

    def execute(args, work, timeout):
        assert args[:2] == ["xvfb-run", "-a"] and timeout == 300
        assert "--arrange" in args and "--slice" in args and "--scale" not in args
        assert args[-1] == "input.stl"
        (work / "output.gcode.3mf").write_bytes(package())
        return 0, "fixture only"

    raw, result = slice_mesh(tmp_path, root, policy.bundle_sha256, execute)
    assert inspect_package(raw)["plateCount"] == 1
    assert result["sourceSha256"] == digest(cube()) and result["dimensionsMm"] == [10, 10, 10]
    (root / "process.json").write_text('{"post_process":["touch /tmp/unsafe"]}')
    with pytest.raises(ValueError):
        load_profile(root, policy.bundle_sha256)


def test_oversized_mesh_never_silently_scales(tmp_path, profile_bundle):
    root, policy = profile_bundle
    (tmp_path / "input.stl").write_bytes(cube(300))
    with pytest.raises(ValueError, match="build volume"):
        slice_mesh(
            tmp_path, root, policy.bundle_sha256, lambda *_: pytest.fail("Do not slice oversized geometry")
        )
    with pytest.raises(ValueError):
        mesh_bounds(b"bad mesh")


def test_local_runner_is_offline_pinned_and_mounts_no_private_extra_files(profile_bundle):
    root, policy = profile_bundle
    (root / ".env").write_text("fixture private configuration, must never mount")

    def runner(args, stdout, **kwargs):
        assert "--read-only" in args and args[args.index("--network") + 1] == "none"
        assert args[args.index("--pull=never") + 1] == "--platform"
        assert policy.image in args and "--privileged" not in args
        mounts = [args[i + 1] for i, v in enumerate(args) if v == "--mount"]
        profile_mount = next(m for m in mounts if "dst=/profiles" in m)
        from pathlib import Path

        staged = Path(profile_mount.split("src=")[1].split(",dst=")[0])
        assert not (staged / ".env").exists()
        stdout.write(
            json.dumps(
                {
                    "schemaVersion": "bambu-slice-v1",
                    "measurements": {"sourceSha256": digest(cube()), "bundleSha256": policy.bundle_sha256},
                    "packageBase64": base64.b64encode(package()).decode(),
                }
            ).encode()
        )
        return SimpleNamespace(returncode=0)

    assert docker_slice(cube(), root, policy, runner=runner) == package()


def test_connect_handoff_is_encoded_and_does_not_start_a_print(tmp_path):
    file = tmp_path / "part.gcode.3mf"
    file.write_bytes(package())
    uri = connect_uri(file)
    assert uri.startswith("bambu-connect://import-file?path=%3Fversion%3Dv1.0.0")
    assert "&" not in uri and "start" not in uri


def test_telemetry_strips_secrets_and_unknown_model_needs_manual_selection():
    data = observation(
        {
            "model_id": "future-model",
            "password": "must not leak",
            "print": {"gcode_state": "RUNNING", "mc_percent": 20, "nozzle_temper": float("nan")},
        }
    )
    assert data["profile"]["modelId"] is None and data["profile"]["modelDetection"] == "select_manually"
    assert "must not leak" not in json.dumps(data) and "nozzleTemperatureC" not in data["profile"]
    assert data["state"] == "running" and data["profile"]["progressPercent"] == 20


@pytest.mark.parametrize("valid_pin", [True, False])
async def test_mqtt_pin_before_credentials_and_subscribe_only(monkeypatch, valid_pin):
    cert = b"fixture certificate"
    topic = "device/FIXTURE123/report"
    incoming = b"\x20\x02\x00\x00\x90\x03\x00\x01\x00" + _packet(
        0x30, _string(topic) + json.dumps({"model_id": "C12", "print": {"gcode_state": "IDLE"}}).encode()
    )

    class Stream:
        def __init__(self):
            self.data, self.sent = bytearray(incoming), []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

        def getpeercert(self, binary_form):
            return cert

        def sendall(self, value):
            self.sent.append(value)

        def recv(self, count):
            result = bytes(self.data[:count])
            del self.data[:count]
            return result

    stream = Stream()

    class Context:
        def __init__(self, *_):
            pass

        def wrap_socket(self, *_args, **_kwargs):
            return stream

    monkeypatch.setattr("kleos.bambu_telemetry.ssl.SSLContext", Context)
    printer = BambuPrinter(
        "192.168.1.20",
        "FIXTURE123",
        "12345678",
        digest(cert) if valid_pin else "0" * 64,
        connector=lambda *_a, **_kw: stream,
    )
    result = await printer.status()
    assert result["connected"] is valid_pin
    assert [p[0] >> 4 for p in stream.sent] == ([1, 8, 14] if valid_pin else [])
    if valid_pin:
        assert result["profile"]["modelId"] == "C12"


def test_mqtt_packet_size_bound():
    class Stream:
        def __init__(self):
            self.data = bytearray(b"\x30\xff\xff\xff\x7f")

        def settimeout(self, _):
            pass

        def recv(self, count):
            value = bytes(self.data[:count])
            del self.data[:count]
            return value

    import time

    with pytest.raises(ValueError):
        _read(Stream(), time.monotonic() + 1)


def test_local_preparation_recovers_published_package_without_reslicing(platform, profile_bundle, tmp_path, monkeypatch):
    root, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    job = start_job(platform, body, ident)
    calls = []
    raw = package()

    def slice_fixture(source, profiles, selected, **_):
        assert source == cube() and profiles == root and selected["bundleSha256"] == policy.bundle_sha256
        calls.append(selected)
        return raw

    def relay(request):
        response = platform.client.request(request.method, request.url.path, content=request.content,
                                           headers={"Content-Type": "application/json"})
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    monkeypatch.setattr("kleos.bambu_local.docker_slice", slice_fixture)
    with httpx.Client(base_url="https://fixture/api/v1", transport=httpx.MockTransport(relay)) as client:
        # Simulate a local save failure after the server has durably published the plate.
        with pytest.raises(FileNotFoundError):
            prepare(client, ident, job["id"], root, tmp_path / "missing" / "plate.3mf")
        destination = tmp_path / "plate.gcode.3mf"
        result = prepare(client, ident, job["id"], root, destination)
        assert result["status"] == "ready_for_operator" and destination.read_bytes() == raw
        prepare(client, ident, job["id"], root, destination)
        assert len(calls) == 1
        destination.write_bytes(b"different local file")
        with pytest.raises(ValueError, match="differs"):
            prepare(client, ident, job["id"], root, destination)


def test_preset_resolution_is_data_only_and_never_self_qualifies(profile_bundle, tmp_path):
    root, policy = profile_bundle
    (root / "base.json").write_text('{"layer_height":"0.25","fill_density":"15%"}')
    (root / "process.json").write_text('{"inherits":"base","layer_height":"0.2"}')
    resolved = resolve_preset(root, "process")
    assert resolved == {"layer_height": "0.2", "fill_density": "15%"}
    destination = tmp_path / "resolved"
    result = build_bundle(root, "machine", "process", "filament", destination, policy.image)
    assert result.qualified is False
    assert load_profile(destination, result.bundle_sha256)["printerModelId"] == "C12"
    assert "inherits" not in (destination / "process.json").read_text()
    (root / "base.json").write_text('{"inherits":"process"}')
    with pytest.raises(ValueError, match="cyclic"):
        resolve_preset(root, "process")


@pytest.mark.parametrize("config", [{"post_process": ["touch /tmp/unsafe"]}, {"inherits": "unresolved"},
                                  {"include": ["unresolved-template"]}])
def test_profile_rejects_commands_even_with_recomputed_hashes(profile_bundle, config):
    root, _ = profile_bundle
    (root / "process.json").write_text(json.dumps(config))
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"]["process.json"] = digest((root / "process.json").read_bytes())
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="post-processing"):
        load_profile(root, digest((root / "manifest.json").read_bytes()))


def test_unqualified_profile_and_changed_idempotency_input_are_rejected(platform, profile_bundle):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    policy.qualified = False
    response = platform.client.post(f"/api/v1/projects/{ident}/print-preparations", json=body,
                                    headers={"Idempotency-Key": "no-qualification"})
    assert response.status_code == 409 and response.json()["code"] == "printer_profile_unqualified"
    policy.qualified = True
    start_job(platform, body, ident)
    platform.settings.bambu_slice_profiles["p1s-other"] = policy
    response = platform.client.post(f"/api/v1/projects/{ident}/print-preparations",
                                    json={**body, "profileId": "p1s-other"},
                                    headers={"Idempotency-Key": "test-preparation"})
    assert response.status_code == 409


def test_json_templates_override_parent_but_not_explicit_preset_fields(tmp_path):
    (tmp_path / "base.json").write_text('{"machine_start_gcode":"base","layer_height":"0.3"}')
    (tmp_path / "template.json").write_text('{"machine_start_gcode":"vendor","layer_height":"0.2"}')
    (tmp_path / "printer.json").write_text('{"inherits":"base","include":["template"],"layer_height":"0.1"}')
    assert resolve_preset(tmp_path, "printer") == {"machine_start_gcode": "vendor", "layer_height": "0.1"}
    (tmp_path / "template.json").write_text('{"include":"printer"}')
    with pytest.raises(ValueError, match="cyclic"):
        resolve_preset(tmp_path, "printer")


def test_existing_preparation_pins_profile_across_operator_changes(platform, profile_bundle):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    job = start_job(platform, body, ident)
    platform.settings.bambu_slice_profiles = {}
    assert start_job(platform, body, ident)["profile"] == job["profile"]
    posted = uploaded_package(platform, ident, policy)
    path = f"/api/v1/projects/{ident}/print-preparations/{job['id']}/complete"
    assert platform.client.post(path, json=posted).status_code == 200
    assert platform.client.post(path, json={**posted, "bundleSha256": "0" * 64}).status_code == 409


def test_oversized_upload_cannot_publish(platform, profile_bundle):
    _, policy = profile_bundle
    ident, body = accepted_source(platform, policy)
    job = start_job(platform, body, ident)
    posted = uploaded_package(platform, ident, policy)
    with platform.db.transaction() as session:
        session.get(Artifact, posted["artifactId"]).size = 17 * 1024 * 1024
    result = platform.client.post(f"/api/v1/projects/{ident}/print-preparations/{job['id']}/complete", json=posted)
    assert result.status_code == 422
