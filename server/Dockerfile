# The board, as Coolify deploys it.
#
# Build context is the repository root, not this directory: the page in `web/`
# and the logo in `assets/` are shared with the booth's own kiosk server, and
# copying them into `server/` would put the same files in the repository twice.
#
# In Coolify: Dockerfile build pack, "Dockerfile Location" = /server/Dockerfile,
# base directory left at /.
FROM python:3.12-slim

# Never write .pyc into a container that is thrown away, and never buffer
# output — an unflushed log line is a deployment that looks hung.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/server \
    WHEEL_RACER_DB=/data/wheel-racer.sqlite3

WORKDIR /app

# Requirements first, so a change to the code does not reinstall the world.
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/wheel_racer_server ./server/wheel_racer_server
COPY web ./web
COPY assets ./assets

# The database lives on a mounted volume, outside everything served. It holds
# email addresses; nothing here should ever be baked into an image.
VOLUME ["/data"]
EXPOSE 8000

# Reported to Coolify, which will not route traffic to a container that has not
# come up. Uses Python rather than curl, which this image does not have.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)"

# One worker on purpose. There is one writer, the data is a single SQLite file,
# and a booth afternoon is a few hundred requests — more workers would buy
# nothing and cost the simplicity of knowing exactly who is writing.
CMD ["uvicorn", "wheel_racer_server.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
