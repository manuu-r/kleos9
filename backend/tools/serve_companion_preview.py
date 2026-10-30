"""Serve one verified candidate web ZIP on a credential-free local preview origin."""
import argparse
import functools
import hashlib
import io
import stat
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from uuid import UUID

import httpx

from kleos.config import get_settings


class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                         "font-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'self'; form-action 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=UUID)
    parser.add_argument("--port", type=int, default=5181)
    args = parser.parse_args()
    if args.port in {5180, 8000}:
        parser.error("Preview must use a separate origin from the authenticated application")
    settings = get_settings()
    if settings.auth_mode != "development" or settings.environment != "development":
        raise SystemExit("This preview helper is for local development only")
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=60, headers={
        "Authorization": f"Bearer {settings.development_token.get_secret_value()}"
    }) as client:
        manifest = client.get(f"/api/v1/artifacts/{args.artifact}/download")
        manifest.raise_for_status()
        response = client.get(f"/api/v1/artifacts/{args.artifact}/content")
        response.raise_for_status()
    content = response.content
    if len(content) > 40 * 1024**2 or hashlib.sha256(content).hexdigest() != manifest.json()["sha256"]:
        raise SystemExit("Artifact integrity or size check failed")
    directory = Path(".data/companion-previews") / str(args.artifact)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 256 * 1024**2:
            raise SystemExit("Preview archive exceeds its extraction limits")
        for entry in entries:
            relative = PurePosixPath(entry.filename)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename
                    or ":" in entry.filename or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.file_size > 64 * 1024**2):
                raise SystemExit("Unsafe preview archive entry")
            target = directory / relative
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    if not (directory / "index.html").is_file():
        raise SystemExit("Preview has no web entry point")
    print(f"Candidate companion preview: http://127.0.0.1:{args.port} (separate origin; no Kleos credentials)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), functools.partial(PreviewHandler, directory=directory)).serve_forever()


if __name__ == "__main__":
    main()
