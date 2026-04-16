FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY apps/worker ./apps/worker

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ./apps/worker

ENV PYTHONPATH=apps/worker
ENV CURRICULUM_TUTOR_WORKER_PROCESSES=2
ENV CURRICULUM_TUTOR_WORKER_THREADS=4

CMD ["sh", "-c", "python -m dramatiq --processes ${CURRICULUM_TUTOR_WORKER_PROCESSES:-2} --threads ${CURRICULUM_TUTOR_WORKER_THREADS:-4} app.jobs.ingestion app.jobs.maintenance"]
