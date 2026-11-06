FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEKNIHY_WORKDIR=/books \
    NEKNIHY_CONFIG_DIR=/config \
    NEKNIHY_WEB_PORT=8478

WORKDIR /app
RUN pip install --no-cache-dir "requests>=2.30.0"

COPY src/neknihy /app/neknihy
COPY src/neknihy_cli.py src/neknihy_web.py /app/

RUN mkdir -p /books /config && chown 1000:1000 /books /config
USER 1000:1000
VOLUME ["/books", "/config"]
EXPOSE 8478

HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('NEKNIHY_WEB_PORT','8478'))" || exit 1

# web UI + scheduler; for a one-shot CLI run use:
#   docker run --rm --entrypoint python IMAGE /app/neknihy_cli.py --once
ENTRYPOINT ["python", "/app/neknihy_web.py"]
