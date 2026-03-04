FROM python:3.12-slim

WORKDIR /app

# Install dependencies directly — no uv needed in the container
COPY pyproject.toml /tmp/pyproject.toml
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.0" \
    "pyyaml>=6.0" \
    "anthropic>=0.40" \
    "openai>=1.50" \
    "sqlalchemy[asyncio]>=2.0" \
    "asyncpg>=0.30" \
    "scikit-learn>=1.4"

# Copy gateway source into /app/gateway/ so `from gateway.x import ...` resolves
COPY . /app/gateway/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV POLICIES_DIR=/app/policies

EXPOSE 8000

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
