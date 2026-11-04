FROM ghcr.io/astral-sh/uv:0.8.11@sha256:8101ad825250a114e7bef89eefaa73c31e34e10ffbe5aff01562740bac97553c AS uv
FROM docker.io/library/python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv UV_NO_CACHE=1
WORKDIR /opt/kleos-mcp
COPY image.pyproject.toml ./pyproject.toml
COPY image.uv.lock ./uv.lock
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python
COPY isolated_server.py ./server.py
COPY tool-schema.json ./tool-schema.json
COPY vendor/source.json ./vendor-source.json
ADD vendor/qwen-mm-core-read-image.tar.gz ./vendor/
RUN test -x /usr/bin/timeout && chmod -R a-w /opt/kleos-mcp
LABEL kleos.mcp.protocol="kleos-mcp-inspection-v1"
LABEL kleos.mcp.plugin="qwen-mm-plugins-core@1.3.0"
LABEL kleos.mcp.commit="33a54695774020e5da2d6b1ed13119d4014f848f"
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/timeout", "--signal=KILL", "30", "/opt/venv/bin/python", "-I", "/opt/kleos-mcp/server.py"]
