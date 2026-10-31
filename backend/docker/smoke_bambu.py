"""Offline image check using a trusted closed cube; never sends a printer command."""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from kleos.bambu_contracts import BambuProfile
from kleos.bambu_local import docker_slice
from kleos.bambu_slicing import inspect_package, load_profile


def cube():
    vertices = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
                (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return (b"Kleos trusted 10mm slicing smoke".ljust(80, b"\0") + struct.pack("<I", len(faces))
            + b"".join(struct.pack("<12fH", 0, 0, 0, *(n for v in face for n in vertices[v]), 0)
                       for face in faces))


def main():
    parser = argparse.ArgumentParser(description="Exercise the pinned offline slicer; never qualifies a physical printer")
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--image", required=True, help="Immutable local sha256 ID or repository digest")
    parser.add_argument("--desktop-compatibility", action="store_true")
    args = parser.parse_args()
    manifest = load_profile(args.profiles, args.bundle_sha256)
    # Only this temporary smoke policy enables execution. No server policy is promoted.
    profile = BambuProfile(printerModelId=manifest["printerModelId"], nozzleMm=manifest["nozzleMm"],
                           material=manifest["material"], bundleSha256=args.bundle_sha256,
                           image=args.image, qualified=True)
    raw = docker_slice(cube(), args.profiles, profile, desktop_compatibility=args.desktop_compatibility)
    print(json.dumps({"schemaVersion": "bambu-image-smoke-v1", "image": args.image,
                      "bundleSha256": args.bundle_sha256, "sourceSha256": hashlib.sha256(cube()).hexdigest(),
                      "packageSha256": hashlib.sha256(raw).hexdigest(), "measurements": inspect_package(raw),
                      "physicalAcceptance": False, "profileQualified": False}, allow_nan=False))


if __name__ == "__main__":
    main()
