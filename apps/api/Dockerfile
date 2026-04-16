FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY apps/api ./apps/api

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ./apps/api

ENV PYTHONPATH=apps/api
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
