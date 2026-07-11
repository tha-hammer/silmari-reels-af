FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

EXPOSE 8899

# Shell form so ${PORT} (injected by Railway) expands at runtime.
CMD gunicorn --bind 0.0.0.0:${PORT:-8899} --workers 2 --timeout 130 server:app
