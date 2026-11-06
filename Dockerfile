FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEKNIHY_WORKDIR=/books \
    NEKNIHY_INTERVAL=21600

WORKDIR /app
RUN pip install --no-cache-dir "requests>=2.30.0"

COPY src/neknihy /app/neknihy
COPY src/neknihy_cli.py /app/neknihy_cli.py

RUN mkdir -p /books && chown 1000:1000 /books
USER 1000:1000
VOLUME ["/books"]

ENTRYPOINT ["python", "/app/neknihy_cli.py"]
