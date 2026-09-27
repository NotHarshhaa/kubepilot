FROM python:3.12-slim

WORKDIR /app

# Run as a non-root user; the kubeconfig is expected to be mounted read-only.
RUN useradd --create-home kubepilot

COPY pyproject.toml README.md LICENSE ./
COPY app ./app

RUN pip install --no-cache-dir . && chown -R kubepilot:kubepilot /app

USER kubepilot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
