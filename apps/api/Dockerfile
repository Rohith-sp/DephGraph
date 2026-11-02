# Build context = repo root (docker-compose.yml). The repo layout is kept so app/config.py's
# ROOT (parents[3]) resolves and scripts/ (seed, dev token) run from the same image.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 HF_HOME=/hf-cache
WORKDIR /srv/dephgraph
COPY apps/api/requirements.txt apps/api/requirements.txt
# CPU-only torch first (the default wheel drags in ~2.5 GB of CUDA); the pin in requirements is then satisfied.
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r apps/api/requirements.txt
RUN useradd -m app && mkdir /hf-cache /shared && chown app /hf-cache /shared
COPY apps/api apps/api
COPY scripts scripts
USER app
WORKDIR /srv/dephgraph/apps/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
