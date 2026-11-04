# Fresh control-plane image only. Generated engineering code runs in separate native images.
# Official multi-platform pins resolved 2026-11-03; uv matches the existing local tool version.
ARG PYTHON_IMAGE=docker.io/library/python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.11@sha256:8101ad825250a114e7bef89eefaa73c31e34e10ffbe5aff01562740bac97553c
FROM ${UV_IMAGE} AS uv_tool
FROM ${PYTHON_IMAGE}
COPY --from=uv_tool /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/src
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Install only locked runtime dependencies. Source imports avoid an extra unlocked build backend.
RUN test "$(uv --version | cut -d' ' -f2)" = "0.8.11" \
    && uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY docs/companion-native-font-browser.json ./docs/companion-native-font-browser.json
RUN python -c "from kleos.infrastructure.runtime import migration_config; from alembic.script import ScriptDirectory; assert ScriptDirectory.from_config(migration_config()).get_heads()" \
    && mkdir -p /var/lib/kleos/artifacts /var/lib/kleos/native /var/lib/kleos/checkers /var/lib/kleos/slicing \
    && chown -R 10001:10001 /var/lib/kleos
USER 10001:10001
EXPOSE 8000 8004
ENTRYPOINT ["python", "-m", "kleos.cli"]
# Serving never starts objective workers or migrates a database implicitly.
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
