"""Serve the built app on the local network without stale bootstrap caches."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class WebHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


class PreviewServer(ThreadingHTTPServer):
    # A fresh Flutter page requests several assets concurrently. Keep bursts from
    # desktop and phone clients from overflowing the standard five-slot backlog.
    request_queue_size = 128
    daemon_threads = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5180)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1] / 'build' / 'web'
    if not (directory / 'index.html').is_file():
        parser.error('Build the Flutter web app before starting the server.')
    server = PreviewServer(
        ('0.0.0.0', args.port), partial(WebHandler, directory=str(directory)))
    print(f'Serving {directory} on port {args.port}', flush=True)
    server.serve_forever()
