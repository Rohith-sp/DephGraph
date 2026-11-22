# Build context = repo root (docker-compose.yml). The repo layout is kept so app/config.py's
# ROOT (parents[3]) resolves and scripts/ (seed, dev token) run from the same image.
FROM python:3.12.16-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# CPU-only torch first, before requirements, so editing requirements.txt keeps this ~200 MB layer cached
# (the default wheel drags in ~2.5 GB of CUDA; the torch pin in requirements is then already satisfied).
RUN pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
COPY apps/api/requirements.txt requirements.txt
RUN pip install -r requirements.txt

FROM python:3.12.16-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HOME=/hf-cache PATH=/opt/venv/bin:$PATH
RUN useradd -m app && mkdir /hf-cache /shared && chown app /hf-cache /shared
COPY --from=build /opt/venv /opt/venv
WORKDIR /srv/dephgraph
COPY apps/api apps/api
COPY scripts scripts
USER app
WORKDIR /srv/dephgraph/apps/api
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=20 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/health')"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
