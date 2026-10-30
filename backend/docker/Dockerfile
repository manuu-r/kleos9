FROM python:3.12.11-slim-bookworm AS control
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYDANTIC_AI_NO_BANNER=1
WORKDIR /opt/kleos
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd --uid 1000 --create-home kleos
COPY pyproject.toml ./
COPY kleos ./kleos
COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/bootstrap_database.py ./bootstrap_database.py
COPY docker/deploy.py ./deploy.py
RUN pip install --no-cache-dir --no-deps .
USER 1000
EXPOSE 8000
CMD ["uvicorn", "kleos.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
