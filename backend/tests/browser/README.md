# Browser end-to-end helpers

`e2e_lib.js` launches headless Chromium, opens the built Flutter web app (`http://localhost:5180/` by default),
turns on Flutter's accessibility tree and lets a script find and click elements by label, so the real UI can be
driven like a user. Build the app against a running backend with
`flutter build web --dart-define=KLEOS_BACKEND=production --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1`
and serve the output on port 5180 (the backend's default allowed frontend origin).
