FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY extract_pdf.py .
COPY app ./app
COPY run.py .

ENV MAX_CONCURRENT_JOBS=8 \
    MAX_UPLOAD_MB=25 \
    TITLE_MAX_PAGES=2 \
    PYTHONPATH=/app

EXPOSE 8000

# Do NOT pass ${PORT} through shell/exec — Railway may not expand it.
# run.py reads PORT from the environment as an integer.
CMD ["python", "run.py"]
