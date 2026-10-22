
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    requests

COPY main.py /app/main.py

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "main.py"]