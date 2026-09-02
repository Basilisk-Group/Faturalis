# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first, in their own layer, so editing app source never
# invalidates this layer's cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Now the project itself.
COPY qr_bench ./qr_bench
COPY static ./static
COPY scripts ./scripts
RUN uv sync --frozen --no-dev


FROM python:3.11-slim AS runtime

# libzbar0: required by pyzbar (loaded via ctypes at import time, not a
# build-time dependency). libglib2.0-0 + libgomp1: runtime shared libs
# opencv-python-headless still needs even without any X11/GUI stack -
# it's headless, not dependency-free. Do NOT use opencv-python (non
# -headless) here, it pulls in libGL/X11 that don't exist in this image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libzbar0 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 qrbench && \
    useradd --uid 1000 --gid qrbench --create-home --shell /usr/sbin/nologin qrbench

WORKDIR /app

COPY --from=builder --chown=qrbench:qrbench /app/.venv /app/.venv
COPY --chown=qrbench:qrbench qr_bench ./qr_bench
COPY --chown=qrbench:qrbench static ./static
COPY --chown=qrbench:qrbench scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN mkdir -p /app/data /app/inbox /app/inbox/_processed /app/uploads /app/samples && \
    chown -R qrbench:qrbench /app/data /app/inbox /app/uploads /app/samples

USER qrbench

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "qr_bench.main:app", "--host", "0.0.0.0", "--port", "8000"]
