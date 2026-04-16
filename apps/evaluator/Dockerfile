FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY apps/evaluator ./apps/evaluator

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ./apps/evaluator

ENV PYTHONPATH=apps/evaluator

CMD ["python", "-m", "app.main"]
