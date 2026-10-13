# AgentGrow Bookings: the FastAPI app (WhatsApp webhook, judge demo, Friday API). Data lives in /app/data (mount a volume).
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.12.17
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY bookings ./bookings
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 BOOKINGS_DB=/app/data/bookings.db
RUN useradd --uid 10001 app && mkdir -p /app/data && chown app /app/data
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/demo', timeout=4)"
CMD ["uvicorn", "bookings.app:default_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
