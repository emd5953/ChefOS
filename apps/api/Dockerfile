FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.10.9

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# In Compose the source is bind-mounted over /app; this copy makes the image self-contained.
COPY . .

# One image, two processes: the Compose file picks the command (api or worker).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
