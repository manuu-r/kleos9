"""Entrypoint for the isolated, disposable native worker image.

Never run this module in the API container or on a developer's host. Generated
build scripts execute under a separate UID without the callback credential.
Validators inspect exported files; they do not accept generator-written PASSes.
"""
import base64
import ctypes
import hashlib
import json
import math
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from .contracts import CheckResult, GeneratedFile, JobResult

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_WORK_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
WORKER_UID = 10001
LANES = {"mechanics", "electronics", "firmware", "application", "coordination", "review", "research"}
RENODE_VERSION = "1.15.3"
RENODE_PROFILE = "stm32f4-uart-v1"
RENODE_QUALIFICATION = Path("/opt/kleos/renode/qualification.json")
OFFLINE_FONT_DIR = Path("/opt/flutter/bin/cache/artifacts/material_fonts")
OFFLINE_FONT_HASHES = {
    "Roboto-Regular.ttf": "79e851404657dac2106b3d22ad256d47824a9a5765458edb72c9102a45816d95",
    "Roboto_LICENSE.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}
RENODE_SCRIPT = """mach create "kleos-uart-smoke"
machine LoadPlatformDescription @platforms/boards/stm32f4_discovery.repl
sysbus LoadELF @{elf}
sysbus.usart2 CreateFileBackend @{uart} true
emulation RunFor "2"
sysbus.usart2 CloseFileBackend @{uart}
quit
"""


class UnsupportedTool(Exception):
    pass


class NativeBuildError(RuntimeError):
    def __init__(self, message, logs=""):
        super().__init__(message)
        self.logs = logs[-100000:]


class NativeResourceError(NativeBuildError):
    pass


class NativeTimeoutError(TimeoutError):
    def __init__(self, message, logs=""):
        super().__init__(message)
        self.logs = logs[-100000:]


def safe_relative(name):
    path = PurePosixPath(name)
    if not path.name or path.is_absolute() or ".." in path.parts or "\\" in name or "\x00" in name:
        raise ValueError("Unsafe artifact path")
    return path


def stage_inputs(request, directory):
    total = 0
    seen = set()
    staged = {}
    for artifact in request["inputArtifacts"]:
        lane = artifact["lane"]
        if lane not in LANES:
            raise ValueError("Unknown engineering lane")
        relative = safe_relative(artifact["name"])
        identity = (lane, str(relative))
        if identity in seen:
            raise ValueError("Ambiguous duplicate file in candidate closure")
        seen.add(identity)
        content = base64.b64decode(artifact["contentBase64"], validate=True)
        total += len(content)
        if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Input artifact limit exceeded")
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise ValueError("Input artifact hash mismatch")
        path = directory / lane / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        staged[artifact["id"]] = path
    return staged


def clean_environment(directory):
    # Deliberately do not inherit AWS, Modal, API credentials, callback token or PYTHONPATH.
    return {
        "PATH": "/opt/venv/bin:/opt/flutter/bin:/opt/dart-sdk/bin:/opt/toolchains/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(directory / ".home"),
        "TMPDIR": str(directory / ".tmp"),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PUB_CACHE": "/opt/pub-cache", "ZEPHYR_BASE": "/opt/zephyrproject/zephyr",
        "ZEPHYR_TOOLCHAIN_VARIANT": "gnuarmemb", "GNUARMEMB_TOOLCHAIN_PATH": "/usr", "CI": "true",
        "CMAKE_BUILD_PARALLEL_LEVEL": "2", "MAKEFLAGS": "-j2",
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(directory / ".tmp/dotnet"), "DOTNET_EnableDiagnostics": "0",
    }


def unprivileged_limits(timeout):
    def prepare():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # Compilers produce larger intermediate IR than the transferable artifacts.
        # Per-task scratch/memory quotas remain independently enforced.
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WORK_FILE_BYTES, MAX_WORK_FILE_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
        if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError("Could not restrict subprocess privilege escalation")
        os.setgroups([])
        os.setgid(WORKER_UID)
        os.setuid(WORKER_UID)
    return prepare


def reap_generated_processes():
    """A fresh native task has one generated UID; stop detached descendants too."""
    child = os.fork()
    if child == 0:
        try:
            os.setgroups([])
            os.setgid(WORKER_UID)
            os.setuid(WORKER_UID)
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            os._exit(0)
    os.waitpid(child, 0)


def run_command(args, directory, timeout):
    if not shutil.which(args[0], path=clean_environment(directory)["PATH"]):
        raise UnsupportedTool(f"Required tool is absent: {args[0]}")
    for name in (".home", ".tmp"):
        (directory / name).mkdir(exist_ok=True)
    for path in [directory, *directory.rglob("*")]:
        if not path.is_symlink():
            os.chown(path, WORKER_UID, WORKER_UID)
    # Bounded file-backed logs avoid unbounded PIPE buffering from generated code.
    # The generated UID cannot replace or symlink the supervisor's temporary log.
    with tempfile.TemporaryFile(mode="w+b") as output:
        process = subprocess.Popen(
            args, cwd=directory, env=clean_environment(directory),
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=unprivileged_limits(timeout),
        )
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            timed_out = True
        finally:
            reap_generated_processes()
        output.seek(0)
        logs = output.read(100000).decode(errors="replace")
        if timed_out:
            raise NativeTimeoutError("Native subprocess exceeded its time limit", logs)
    return code, logs


def collect_file(path, base, name=None):
    if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("Output path escapes the job workspace")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("Output artifact exceeds 16 MiB")
    content = path.read_bytes()
    return GeneratedFile(path=name or str(path.relative_to(base)), content=base64.b64encode(content).decode(),
                         media_type="application/octet-stream", encoding="base64")


MECHANICAL_BUILD = """
from pathlib import Path
import runpy
from build123d import export_step, export_stl
scope = runpy.run_path('enclosure.py')
assembly = scope.get('assembly')
if assembly is None:
    raise ValueError('enclosure.py must define assembly')
Path('outputs').mkdir(exist_ok=True)
export_step(assembly, 'outputs/model.step')
export_stl(assembly, 'outputs/model.stl')
"""

MECHANICAL_INSPECT = """
import json, sys
from build123d import Box, import_step
shape = import_step(sys.argv[1])
bounds = shape.bounding_box()
box = bounds.size
contract = json.loads(sys.argv[2])
axes = ('x', 'y', 'z')
inner = [float(contract['internalMm'][axis]) for axis in axes]
origin = [value + (outer - inside) / 2 for value, outer, inside in zip(
    (bounds.min.X, bounds.min.Y, bounds.min.Z), (box.X, box.Y, box.Z), inner)]
clearance = Box(*inner).translate(tuple(origin[i] + inner[i] / 2 for i in range(3)))
obstruction = shape & clearance
collisions = []
for component in contract['components']:
    dimensions = [float(component['sizeMm'][axis]) for axis in axes]
    center = tuple(origin[i] + float(component['positionMm'][axis]) + dimensions[i] / 2
                   for i, axis in enumerate(axes))
    intersection = shape & Box(*dimensions).translate(center)
    collisions.append({'id': component['id'], 'intersectionMm3': intersection.volume if intersection else 0.0})
valid = shape.is_valid
if callable(valid):
    valid = valid()
print(json.dumps({'sizeMm': {'x': box.X, 'y': box.Y, 'z': box.Z},
                  'volumeMm3': shape.volume, 'solidCount': len(shape.solids()), 'valid': bool(valid),
                  'internalObstructionMm3': obstruction.volume if obstruction else 0.0,
                  'componentIntersections': collisions}))
"""


PROTOCOL_C_HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "watch_protocol.h"
int main(int argc, char **argv) {
  for (int a=1; a<argc; ++a) {
    uint8_t bytes[128]; size_t n=0;
    for (char *p=argv[a]; *p && p[1] && n<128; p+=2) {
      char pair[3] = {p[0],p[1],0}; bytes[n++] = (uint8_t)strtoul(pair, NULL, 16);
    }
    struct kleos_telemetry out = {0};
    if (kleos_telemetry_decode(bytes,n,&out) == 1)
      printf("{\"batteryPercent\":%u,\"heartRateBpm\":%u,\"steps\":%u}\n",
             (unsigned)out.battery_percent,(unsigned)out.heart_rate_bpm,(unsigned)out.steps);
    else puts("null");
  }
  return 0;
}
"""

PROTOCOL_DART_HARNESS = """
import 'dart:convert';
import 'lib/watch_protocol.dart';
void main(List<String> args) {
  for (final hex in args) {
    final bytes = [for (var i=0; i<hex.length; i+=2) int.parse(hex.substring(i,i+2),radix:16)];
    final value = WatchTelemetry.decode(bytes);
    print(jsonEncode(value == null ? null : {
      'batteryPercent': value.batteryPercent, 'heartRateBpm': value.heartRateBpm, 'steps': value.steps,
    }));
  }
}
"""


def protocol_vectors():
    """Independent wire expectations: version/u8 battery/u16 BPM/u32 steps, little-endian."""
    import random
    import struct
    frames = [b"", b"\x01", b"\x01" * 7, b"\x01" * 9,
              struct.pack("<BBHI", 0, 50, 80, 100), struct.pack("<BBHI", 1, 101, 80, 100),
              struct.pack("<BBHI", 1, 50, 241, 100)]
    randomizer = random.Random(907013)
    for battery, bpm, steps in [(0, 0, 0), (100, 240, 4294967295),
                                 (50, 75, 123456), *[(randomizer.randrange(101), randomizer.randrange(241),
                                                    randomizer.randrange(2**32)) for _ in range(20)]]:
        frames.append(struct.pack("<BBHI", 1, battery, bpm, steps))
    expected = []
    for frame in frames:
        if len(frame) != 8 or frame[0] != 1 or frame[1] > 100 or int.from_bytes(frame[2:4], "little") > 240:
            expected.append(None)
        else:
            expected.append({"batteryPercent": frame[1], "heartRateBpm": int.from_bytes(frame[2:4], "little"),
                             "steps": int.from_bytes(frame[4:], "little")})
    return [frame.hex() for frame in frames], expected


def validate_protocol(lane, work, execute):
    frames, expected = protocol_vectors()
    if lane == "firmware":
        source = work / "src/watch_protocol.c"
        header = work / "src/watch_protocol.h"
        if not source.is_file() or not header.is_file():
            raise UnsupportedTool("watch-telemetry-v1 requires src/watch_protocol.c and .h")
        harness = work / ".kleos_protocol_harness.c"
        harness.write_text(PROTOCOL_C_HARNESS)
        binary = work / ".kleos_protocol_harness"
        code, output = execute(["cc", "-std=c11", "-Wall", "-Werror", "-I", str(header.parent),
                                str(harness), str(source), "-o", str(binary)], work, 120)
        if code:
            raise RuntimeError(f"Independent C protocol harness did not compile: {output[-2000:]}")
        args = [str(binary), *frames]
    else:
        if not (work / "lib/watch_protocol.dart").is_file():
            raise UnsupportedTool("watch-telemetry-v1 requires lib/watch_protocol.dart")
        harness = work / ".kleos_protocol_harness.dart"
        harness.write_text(PROTOCOL_DART_HARNESS)
        args = ["dart", str(harness), *frames]
    code, output = execute(args, work, 120)
    if code:
        raise RuntimeError(f"Independent protocol harness failed: {output[-2000:]}")
    try:
        actual = [json.loads(line) for line in output.strip().splitlines()]
    except ValueError as exc:
        raise RuntimeError("Protocol harness did not return valid frame results") from exc
    if actual != expected:
        raise RuntimeError("Protocol decoding failed fixed independent boundary/invalid-frame fixtures")
    return {"profile": "watch-telemetry-v1", "fixtureCount": len(expected),
            "coverage": "Independent eight-byte telemetry decoding, boundary values and invalid-frame rejection"}


def native_exports(request, directory, lane, suffix):
    """Only server-attributed tool outputs may satisfy compiled/exported artifact checks."""
    return [directory / lane / safe_relative(ref["name"]) for ref in request["inputArtifacts"]
            if ref["lane"] == lane and ref.get("provenance", {}).get("nativeJobId")
            and ref["name"].endswith(suffix)]


def run_renode_uart(elf_bytes, work, execute=run_command):
    """Actual Renode process contract; also usable by an operator qualification fixture."""
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(work)):
        raise UnsupportedTool("Renode workspace path is outside the fixed safe-path profile")
    work.mkdir(parents=True, exist_ok=True)
    elf, uart, script = work / "firmware.elf", work / "uart.log", work / "scenario.resc"
    elf.write_bytes(elf_bytes)
    uart.unlink(missing_ok=True)
    script.write_text(RENODE_SCRIPT.format(elf=elf, uart=uart))
    code, version = execute(["renode", "-v"], work, 15)
    if code or not re.search(r"\b1\.15\.3(?!\d)", version):
        raise UnsupportedTool(f"Renode {RENODE_VERSION} is required by the pinned UART scenario")
    code, logs = execute(["renode", "--console", "--disable-xwt", "--plain", str(script)], work, 60)
    if code:
        raise RuntimeError(f"Renode scenario failed: {logs[-2000:]}")
    if not uart.is_file() or uart.is_symlink() or not uart.resolve().is_relative_to(work.resolve()):
        raise RuntimeError("Renode produced no safe UART capture")
    with uart.open("rb") as stream:
        capture = stream.read(100001)
    if len(capture) > 100000:
        raise RuntimeError("Renode UART output exceeded its capture limit")
    output = capture.decode(errors="replace")
    if "KLEOS_UART_SMOKE_V1" not in {line.strip() for line in output.splitlines()}:
        raise RuntimeError("Expected boot marker was not observed on emulated USART2")
    return {
        "profile": RENODE_PROFILE, "renodeVersion": RENODE_VERSION, "simulatedSeconds": 2,
        "elfSha256": hashlib.sha256(elf_bytes).hexdigest(),
        "uartSha256": hashlib.sha256(capture).hexdigest(), "uartCapture": output[-4000:],
        "executionMode": "digital_simulation", "required": True, "scopeExcluded": False,
        "coverage": "STM32F4 instruction execution and boot-marker emission through emulated USART2 only",
        "limitations": ["Does not qualify BLE, sensors, display, radio, thermal or physical operation"],
    }


def validate_renode(request, directory, execute=run_command, qualification_path=RENODE_QUALIFICATION):
    """Only an operator-qualified, pinned image can produce a Renode PASS."""
    details = {"required": True, "scopeExcluded": False, "executionMode": "digital_simulation"}
    try:
        if request.get("requirements", {}).get("renodeProfile") != RENODE_PROFILE:
            raise UnsupportedTool("The requested Renode profile is unsupported")
        if request.get("interfaces", {}).get("firmware", {}).get("board") != "stm32f4_disco":
            raise UnsupportedTool("The UART scenario requires the stm32f4_disco Zephyr board")
        if not qualification_path.is_file():
            raise UnsupportedTool("The Renode image/profile has no operator qualification record")
        qualification = json.loads(qualification_path.read_text())
        expected_hash = hashlib.sha256(RENODE_SCRIPT.encode()).hexdigest()
        if (not isinstance(qualification, dict) or qualification.get("qualified") is not True
                or qualification.get("profile") != RENODE_PROFILE
                or qualification.get("renodeVersion") != RENODE_VERSION
                or qualification.get("scenarioSha256") != expected_hash):
            raise UnsupportedTool("The pinned Renode image/profile has not passed qualification")
        refs = [ref for ref in request["inputArtifacts"] if ref["lane"] == "firmware"
                and ref["name"].endswith(".elf") and ref.get("provenance", {}).get("nativeJobId")]
        if len(refs) != 1:
            raise UnsupportedTool("Renode requires one actual native firmware ELF")
        content = base64.b64decode(refs[0]["contentBase64"], validate=True)
        if hashlib.sha256(content).hexdigest() != refs[0]["sha256"]:
            raise RuntimeError("Renode ELF does not match the pinned artifact")
        # Restore immutable bytes into a fresh directory after arbitrary C harness execution.
        with tempfile.TemporaryDirectory(prefix="renode-", dir=directory) as tmp:
            details.update(run_renode_uart(content, Path(tmp), execute))
        return CheckResult(name="renodeUart", status="PASS", details=details)
    except UnsupportedTool as exc:
        return CheckResult(name="renodeUart", status="UNKNOWN", details={**details, "reason": str(exc)})
    except (RuntimeError, ValueError, OSError, TimeoutError) as exc:
        return CheckResult(name="renodeUart", status="FAIL", details={**details, "reason": str(exc)})


def prepare_companion(work, font_dir=OFFLINE_FONT_DIR, expected_hashes=OFFLINE_FONT_HASHES):
    """Make the default font local and record the exact prepared source inputs."""
    import yaml

    pubspec = work / "pubspec.yaml"
    if not pubspec.is_file() or pubspec.stat().st_size > 256 * 1024:
        raise ValueError("Companion requires a bounded pubspec.yaml")
    specification = yaml.safe_load(pubspec.read_text())
    if not isinstance(specification, dict):
        raise ValueError("Companion pubspec must contain a mapping")
    flutter = specification.setdefault("flutter", {})
    if not isinstance(flutter, dict):
        raise ValueError("Companion flutter configuration must contain a mapping")
    fonts = flutter.setdefault("fonts", [])
    if not isinstance(fonts, list) or any(not isinstance(item, dict) for item in fonts):
        raise ValueError("Companion fonts must contain a list of font families")
    destination = work / "assets/kleos_fonts"
    manifest = work / "kleos-build.json"
    if destination.exists() or manifest.exists():
        raise ValueError("Companion uses a reserved build-preparation path")
    destination.mkdir(parents=True)
    for name, expected in expected_hashes.items():
        original = font_dir / name
        if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != expected:
            raise UnsupportedTool("The pinned offline Roboto font or license is unavailable")
        shutil.copyfile(original, destination / name)
    if not any(item.get("family") == "Roboto" for item in fonts):
        fonts.append({"family": "Roboto", "fonts": [{"asset": "assets/kleos_fonts/Roboto-Regular.ttf"}]})
    pubspec.write_text(yaml.safe_dump(specification, sort_keys=False))
    details = {
        "version": 1, "preparation": "offline-default-roboto-v1",
        "fontSource": "Flutter 3.35.4 pinned material_fonts cache",
        "fontLicense": "Apache-2.0", "fontHashes": expected_hashes,
        "sourceHashes": {str(path.relative_to(work)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(work.rglob("*")) if path.is_file()},
    }
    manifest.write_text(json.dumps(details, sort_keys=True, indent=2))
    return details


def build_lane(request, directory, execute=run_command):
    lane, timeout = request["lane"], min(int(request.get("timeoutSeconds", 1800)), 3600)
    work = directory / lane
    work.mkdir(exist_ok=True)
    outputs, logs, prepared_files = [], [], []
    measurements = {}

    def call(args):
        code, output = execute(args, work, timeout)
        logs.append(output)
        resource_error = re.search(
            r"exited abnormally with exit code -(9|25)\b|Could not start thread.*Resource temporarily unavailable"
            r"|Failed to start thread|Cannot allocate memory|Out of memory", output, re.IGNORECASE | re.DOTALL,
        )
        if code in {-9, -25, 137, 153} or (code and resource_error):
            raise NativeResourceError(f"{args[0]} was terminated by the compute resource limit", "\n".join(logs))
        if code:
            raise NativeBuildError(f"{args[0]} failed with exit code {code}", "\n".join(logs))

    if lane == "mechanics":
        if not (work / "enclosure.py").is_file():
            raise UnsupportedTool("Mechanical source must provide enclosure.py defining assembly")
        call(["python", "-I", "-c", MECHANICAL_BUILD])
        outputs = [work / "outputs/model.step", work / "outputs/model.stl"]
    elif lane == "electronics":
        if not (work / "ato.yaml").exists():
            raise UnsupportedTool("Electronics source must include an atopile project")
        call(["ato", "build"])
        boards = list((work / "build").rglob("*.kicad_pcb")) or list(work.rglob("*.kicad_pcb"))
        schematics = list((work / "build").rglob("*.kicad_sch")) or list(work.rglob("*.kicad_sch"))
        if len(boards) != 1 or len(schematics) != 1:
            raise UnsupportedTool("The qualified electronics profile requires one PCB and one schematic")
        (work / "outputs").mkdir(exist_ok=True)
        call(["kicad-cli", "sch", "erc", "--format", "json", "--exit-code-violations",
              "--output", "outputs/erc.json", str(schematics[0])])
        call(["kicad-cli", "pcb", "drc", "--format", "json", "--exit-code-violations",
              "--output", "outputs/drc.json", str(boards[0])])
        call(["kicad-cli", "pcb", "export", "gerbers", "--output", "outputs/gerbers/", str(boards[0])])
        call(["kicad-cli", "pcb", "export", "drill", "--output", "outputs/gerbers/", str(boards[0])])
        outputs = [*boards, *schematics, *[p for p in (work / "outputs").rglob("*") if p.is_file()]]
    elif lane == "firmware":
        board = request.get("interfaces", {}).get("firmware", {}).get("board")
        if not board or not re.fullmatch(r"[A-Za-z0-9_./-]{1,100}", board) or ".." in board:
            raise UnsupportedTool("A qualified Zephyr board must be pinned in interfaces.firmware.board")
        args = ["west", "build", "-b", board, "-d", "build", "."]
        if board.startswith("native_sim"):
            args += ["--", "-DZEPHYR_TOOLCHAIN_VARIANT=host"]
        call(args)
        outputs = [work / "build/zephyr/zephyr.elf", work / "build/zephyr/zephyr.bin"]
    elif lane == "application":
        preparation = prepare_companion(work)
        # Capture before generated tools run, outside their writable directory.
        source_archive = directory / "companion-source.zip"
        with zipfile.ZipFile(source_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(work.rglob("*")):
                if path.is_file():
                    if path.is_symlink() or not path.resolve().is_relative_to(work.resolve()):
                        raise ValueError("Companion source escapes its preparation directory")
                    bundle.write(path, str(path.relative_to(work)))
        prepared_files.append(collect_file(source_archive, directory, "native/companion-source.zip"))
        measurements = {"preparedSourceSha256": hashlib.sha256(source_archive.read_bytes()).hexdigest(),
                        "preparation": preparation["preparation"]}
        call(["flutter", "pub", "get", "--offline"])
        call(["flutter", "analyze", "--no-pub"])
        call(["flutter", "build", "web", "--no-pub", "--no-web-resources-cdn", "--no-wasm-dry-run"])
        for name, expected in preparation.get("sourceHashes", {}).items():
            path = work / name
            if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(work.resolve())
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError("Generated build modified the captured companion source")
        web = work / "build/web"
        license_file = work / "assets/kleos_fonts/Roboto_LICENSE.txt"
        if license_file.is_file():
            target = web / "Roboto_LICENSE.txt"
            if target.is_symlink():
                raise ValueError("Companion license output escapes its build directory")
            shutil.copyfile(license_file, target)
        archive = work / "companion-web.zip"
        archive.unlink(missing_ok=True)
        total = 0
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(web.rglob("*")):
                if not path.is_file():
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(web.resolve()):
                    raise ValueError("Companion output escapes its build directory")
                total += path.stat().st_size
                if total > 128 * 1024**2:
                    raise ValueError("Companion web package exceeds the 128 MiB unpacked ceiling")
                bundle.write(path, str(path.relative_to(web)))
        # Retain inspectable entrypoints alongside the complete compressed bundle.
        outputs = [archive, web / "index.html", web / "main.dart.js"]
    else:
        raise UnsupportedTool(f"No native build profile for {lane}")
    files = [*prepared_files, *[collect_file(p, work, f"native/{p.relative_to(work)}") for p in outputs if p.exists()]]
    if not files:
        raise RuntimeError("The native tool produced no supported outputs")
    if sum(len(f.content) for f in files) > MAX_TOTAL_BYTES:
        raise ValueError("Native output package exceeds the result transfer limit")
    return JobResult(status="succeeded", files=files, measurements=measurements, logs="\n".join(logs)[-100000:],
                     limitations=["Build outputs are candidates until independently validated"])


def _positive_vector(value):
    if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
        raise ValueError("Expected x/y/z dimensions in millimetres")
    result = {k: float(v) for k, v in value.items()}
    if any(not math.isfinite(v) or v <= 0 for v in result.values()):
        raise ValueError("Dimensions must be finite and positive")
    return result


def validate_interfaces(interfaces):
    try:
        mechanics, power = interfaces["mechanical"], interfaces["power"]
        inner, outer = _positive_vector(mechanics["internalMm"]), _positive_vector(mechanics["externalMm"])
        wall = float(mechanics["minimumWallMm"])
        components, loads = mechanics["components"], power["loads"]
        if not math.isfinite(wall) or wall <= 0 or not components or not loads:
            raise ValueError("Walls, component envelopes and power loads must be declared")
        failures = []
        for axis in inner:
            if outer[axis] - inner[axis] < 2 * wall:
                failures.append(f"{axis}: minimum wall allowance is not satisfied")
        for component in components:
            size = _positive_vector(component["sizeMm"])
            position = component["positionMm"]
            for axis in size:
                offset = float(position[axis])
                if not math.isfinite(offset) or offset < 0 or offset + size[axis] > inner[axis]:
                    failures.append(f"{component['id']}: envelope exceeds internal bounds on {axis}")
        for index, first in enumerate(components):
            for second in components[index + 1:]:
                overlap = all(
                    min(float(first["positionMm"][axis]) + float(first["sizeMm"][axis]),
                        float(second["positionMm"][axis]) + float(second["sizeMm"][axis]))
                    - max(float(first["positionMm"][axis]), float(second["positionMm"][axis])) > 1e-6
                    for axis in ("x", "y", "z")
                )
                if overlap:
                    failures.append(f"{first['id']} and {second['id']}: component envelopes overlap")
        voltage, current = float(power["supplyVoltageV"]), float(power["maxCurrentMa"])
        if not all(math.isfinite(n) and n > 0 for n in (voltage, current)):
            raise ValueError("Power supply values must be finite and positive")
        total = 0
        for load in loads:
            low, high, draw = map(float, (load["minVoltageV"], load["maxVoltageV"], load["currentMa"]))
            if not all(math.isfinite(n) for n in (low, high, draw)) or draw < 0 or low > high:
                raise ValueError("Invalid load range")
            total += draw
            if not low <= voltage <= high:
                failures.append(f"{load['id']}: incompatible supply voltage")
        if total > current:
            failures.append("Total current exceeds supply budget")
        return CheckResult(name="interfaces", status="FAIL" if failures else "PASS", details={
            "failures": failures, "totalCurrentMa": total,
            "coverage": "Rectangular component placement/overlap, minimum-wall allowance and declared DC budgets",
            "limitations": ["Does not establish as-built clearances, actual minimum wall thickness, thermal, RF or physical safety"],
        })
    except (KeyError, TypeError, ValueError) as exc:
        return CheckResult(name="interfaces", status="UNKNOWN", details={"reason": str(exc)})


def validate_candidates(request, directory, execute=run_command):
    checks = [
        validate_interfaces(request.get("interfaces", {})),
        CheckResult(name="electronics_correspondence", status="UNKNOWN", details={
            "required": True, "scopeExcluded": False,
            "reason": "No qualified template binds schematic/PCB components and nets to accepted load/interface IDs",
            "coverage": "ERC/DRC alone cannot establish that the requested circuit was implemented",
        }),
    ]
    logs = []
    requirements = request.get("requirements", {})
    profile = requirements.get("validationProfile")
    for lane in ("mechanics", "electronics", "firmware", "application"):
        work = directory / lane
        work.mkdir(exist_ok=True)
        try:
            if lane == "mechanics":
                files = native_exports(request, directory, lane, ".step")
                if len(files) != 1:
                    raise UnsupportedTool("A single exported STEP assembly is required")
                contract = request.get("interfaces", {}).get("mechanical", {})
                if not contract.get("internalMm") or not contract.get("components"):
                    raise UnsupportedTool("Centered internal clearance dimensions and component envelopes are required")
                _positive_vector(contract["internalMm"])
                code, output = execute(["python", "-I", "-c", MECHANICAL_INSPECT, str(files[0]),
                                        json.dumps(contract)], work, 120)
                logs.append(output)
                if code:
                    checks.append(CheckResult(name=lane, status="FAIL", details={"reason": "STEP cannot be parsed"}))
                    continue
                measured = json.loads(output.strip().splitlines()[-1])
                expected = _positive_vector(request["interfaces"]["mechanical"]["externalMm"])
                matches = all(abs(float(measured["sizeMm"][a]) - expected[a]) <= 0.1 for a in expected)
                solid = (measured["solidCount"] > 0 and math.isfinite(float(measured["volumeMm3"]))
                         and float(measured["volumeMm3"]) > 0)
                empty = float(measured["internalObstructionMm3"]) <= 1e-4 and all(
                    float(item["intersectionMm3"]) <= 1e-4 for item in measured["componentIntersections"])
                checks.append(CheckResult(name=lane, status="PASS" if measured["valid"] and matches and solid and empty else "FAIL",
                                          details={"measurements": measured, "toleranceMm": 0.1,
                                                   "intersectionToleranceMm3": 1e-4,
                                                   "coordinateConvention": "Centered internal box; component lower corners measured from internal minimum",
                                                   "coverage": "STEP solid validity/dimensions and actual solid intersections with declared clearances",
                                                   "limitations": ["Does not qualify insertion paths, actual wall thickness, manufacturing tolerances or physical safety"]}))
            elif lane == "electronics":
                for suffix, verb in ((".kicad_sch", ["sch", "erc"]), (".kicad_pcb", ["pcb", "drc"])):
                    files = native_exports(request, directory, lane, suffix)
                    if len(files) != 1:
                        raise UnsupportedTool("A single KiCad schematic and PCB are required")
                    code, output = execute(
                        ["kicad-cli", *verb, "--exit-code-violations", "--output", f"/tmp/{verb[1]}.rpt",
                         str(files[0])], work, 300)
                    logs.append(output)
                    if code:
                        raise RuntimeError(f"{verb[1]} failed")
                report = work / ".kleos_netlist.xml"
                report.unlink(missing_ok=True)
                schematic = native_exports(request, directory, lane, ".kicad_sch")[0]
                code, output = execute(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                                        "--output", str(report), str(schematic)], work, 300)
                logs.append(output)
                if code or not report.is_file() or report.is_symlink() or report.stat().st_size > 1024**2:
                    raise RuntimeError("Independent schematic netlist export failed or exceeded its bounds")
                try:
                    netlist = ET.parse(report).getroot()
                except ET.ParseError as exc:
                    raise RuntimeError("Independent schematic netlist is malformed") from exc
                component_count = len(netlist.findall("./components/comp"))
                net_count = len(netlist.findall("./nets/net"))
                if not component_count or not net_count:
                    raise RuntimeError("Empty schematic components or nets cannot satisfy electronics validation")
                checks.append(CheckResult(name="electronics_erc_drc", status="PASS", details={
                    "coverage": "Independent KiCad ERC and DRC",
                    "componentCount": component_count, "netCount": net_count,
                    "limitations": ["No analog, RF, thermal, EMC or physical validation"],
                }))
                checks.append(CheckResult(name=lane, status="UNKNOWN", details={
                    "required": True, "scopeExcluded": False,
                    "reason": "Schematic and PCB correspondence to accepted loads/interfaces is not qualified",
                }))
            elif lane == "firmware":
                files = native_exports(request, directory, lane, ".elf")
                if len(files) != 1:
                    raise UnsupportedTool("A compiled firmware ELF is required")
                code, output = execute(["readelf", "-h", str(files[0])], work, 30)
                logs.append(output)
                if code:
                    raise RuntimeError("Firmware ELF cannot be parsed")
                if profile != "watch-telemetry-v1":
                    raise UnsupportedTool("No qualified firmware validation profile was accepted")
                symbol_code, symbols = execute(["nm", "-g", "--defined-only", str(files[0])], work, 30)
                if symbol_code or not re.search(r"\bkleos_telemetry_decode$", symbols, re.MULTILINE):
                    raise RuntimeError("The compiled firmware does not include the required telemetry decoder")
                details = validate_protocol(lane, work, execute)
                details["elfHeader"] = output[:4000]
                checks.append(CheckResult(name=lane, status="PASS", details=details))
            else:
                if not native_exports(request, directory, lane, "index.html") or not native_exports(request, directory, lane, "main.dart.js"):
                    raise UnsupportedTool("A compiled Flutter web package is required")
                if profile != "watch-telemetry-v1":
                    raise UnsupportedTool("No qualified companion validation profile was accepted")
                details = validate_protocol(lane, work, execute)
                details["build"] = "Flutter web output present; UI and peripheral interaction excluded"
                checks.append(CheckResult(name=lane, status="PASS", details=details))
        except UnsupportedTool as exc:
            checks.append(CheckResult(name=lane, status="UNKNOWN", details={"reason": str(exc)}))
        except (RuntimeError, ValueError, KeyError, TypeError, IndexError, TimeoutError) as exc:
            checks.append(CheckResult(name=lane, status="FAIL", details={"reason": str(exc)}))
    if "renodeProfile" in requirements:
        checks.append(validate_renode(request, directory, execute))
    if profile == "watch-telemetry-v1":
        extra = requirements.get("requiredPeripheralCoverage")
        exclusions = requirements.get("excludedCoverage")
        supported_exclusions = {"ble", "sensor", "display", "radio", "thermal", "physicalOperation"}
        explicit_scope = (isinstance(extra, list) and all(isinstance(x, str) for x in extra)
                          and isinstance(exclusions, list) and all(isinstance(x, str) for x in exclusions)
                          and set(exclusions) == supported_exclusions and not extra)
        checks.append(CheckResult(name="peripheralSimulation", status="UNKNOWN", details={
            "required": not explicit_scope, "scopeExcluded": explicit_scope,
            "reason": "BLE, sensor/display interaction, radio/thermal and physical operation are outside watch-telemetry-v1",
            "requestedCoverage": extra, "acceptedExclusions": exclusions,
            "scopeValid": explicit_scope,
        }))
    failed = any(c.status in {"FAIL", "ERROR"} for c in checks)
    unsupported = any(c.status == "UNKNOWN" and not (
        c.details.get("required") is False and c.details.get("scopeExcluded") is True
    ) for c in checks)
    return JobResult(status="failed" if failed else "unsupported" if unsupported else "succeeded",
                     checks=checks, logs="\n".join(logs)[-100000:],
                     limitations=[str(c.details.get("reason", "")) for c in checks if c.status == "UNKNOWN"])


def run_job(request, directory, execute=run_command):
    if request.get("schemaVersion") != 1:
        raise ValueError("Unsupported job schema")
    stage_inputs(request, directory)
    try:
        if request["purpose"] == "validate":
            return validate_candidates(request, directory, execute)
        if request["purpose"] == "build":
            return build_lane(request, directory, execute)
        raise ValueError("Unsupported job purpose")
    except UnsupportedTool as exc:
        return JobResult(status="unsupported", limitations=[str(exc)])
    except NativeResourceError as exc:
        return JobResult(status="failed", limitations=[str(exc)], logs=exc.logs,
                         measurements={"failureKind": "resource"})
    except TimeoutError as exc:
        return JobResult(status="failed", limitations=[str(exc)], logs=getattr(exc, "logs", ""),
                         measurements={"failureKind": "deadline"})
    except RuntimeError as exc:
        return JobResult(status="failed", limitations=[str(exc)], logs=getattr(exc, "logs", ""),
                         measurements={"failureKind": "engineering"})
    except (ValueError, OSError) as exc:
        return JobResult(status="failed", limitations=[str(exc)])


def main():
    if sys.platform != "linux" or os.geteuid() != 0 or os.environ.get("KLEOS_NATIVE_SANDBOX") != "1":
        raise SystemExit("Native runner requires the isolated Linux worker image; host execution is disabled")
    if sys.argv[1:] == ["--local-files"]:
        # Only the trusted supervisor can access this per-job volume subpath.
        # Generated UID 10001 cannot modify the request or forge the result envelope.
        control = Path("/control")
        raw = (control / "request.json").read_bytes()
        if len(raw) > 64 * 1024**2:
            raise SystemExit("Local native input exceeds its transfer ceiling")
        request = json.loads(raw)
        with tempfile.TemporaryDirectory(prefix="kleos-native-") as tmp:
            root = Path(tmp)
            root.chmod(0o755)
            started = time.monotonic()
            result = run_job(request, root)
            result.measurements["executionSeconds"] = round(time.monotonic() - started, 3)
        envelope = {"jobId": request["jobId"], "requestSha256": hashlib.sha256(raw).hexdigest(),
                    "result": result.model_dump(mode="json", by_alias=True)}
        temporary = control / "result.tmp"
        temporary.write_text(json.dumps(envelope, separators=(",", ":")))
        temporary.chmod(0o644)
        temporary.replace(control / "result.json")
        print(json.dumps({"event": "native.completed", "jobId": request["jobId"],
                          "status": result.status, "traceId": request.get("traceId")}))
        return
    job_id = os.environ.pop("KLEOS_JOB_ID")
    token = os.environ.pop("KLEOS_JOB_TOKEN")
    base = os.environ.pop("KLEOS_INTERNAL_BASE_URL").rstrip("/")
    if not base.startswith("https://") and os.environ.get("KLEOS_ALLOW_INTERNAL_HTTP") != "1":
        raise SystemExit("Native callback endpoint requires HTTPS")
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        response = client.get(f"{base}/api/v1/internal/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        request = response.json()
        trace_id = str(request.get("traceId", "")).replace("-", "")
        if re.fullmatch(r"[a-f0-9]{32}", trace_id) and int(trace_id, 16):
            headers["traceparent"] = f"00-{trace_id}-{os.urandom(8).hex()}-01"
        print(json.dumps({"event": "native.started", "jobId": job_id,
                          "traceId": request.get("traceId"), "purpose": request.get("purpose")}))
        with tempfile.TemporaryDirectory(prefix="kleos-native-") as tmp:
            root = Path(tmp)
            root.chmod(0o755)
            started = time.monotonic()
            result = run_job(request, root)
            result.measurements["executionSeconds"] = round(time.monotonic() - started, 3)
        print(json.dumps({"event": "native.completed", "jobId": job_id, "status": result.status,
                          "traceId": request.get("traceId")}))
        for retry in range(3):
            try:
                response = client.post(f"{base}/api/v1/internal/jobs/{job_id}/result",
                                       headers=headers, json=result.model_dump(mode="json", by_alias=True))
                response.raise_for_status()
                return
            except (httpx.TransportError, httpx.HTTPStatusError):
                if retry == 2:
                    raise
                time.sleep(2 ** retry)


if __name__ == "__main__":
    main()
