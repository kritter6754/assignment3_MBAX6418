# Optional container image (the normal setup is the local one in README.md):
#   docker build -t ai-jukebox . && docker run --env-file .env -p 8080:8080 ai-jukebox
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY jukebox ./jukebox
COPY assets ./assets
RUN uv sync --frozen --no-dev

# Secrets (CLASS_API_KEY, endpoint URLs) come from --env-file at run time, never the image.
# Generated songs live in DATA_DIR (/data); mount a folder there to keep them across restarts.
ENV DATA_DIR=/data HOST=0.0.0.0 PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "ai-jukebox"]
