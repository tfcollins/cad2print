# cad2print on top of the linuxserver OrcaSlicer image: it already carries the orca-slicer
# binary, its ~150 runtime libraries and the Bambu system profiles (docs/spike-cli.md).
# The web-desktop entrypoint (/init, s6) is replaced by uvicorn; nothing graphical runs.
FROM lscr.io/linuxserver/orcaslicer:v2.4.2-ls42

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PROFILES_FILE=/app/profiles.yaml \
    HANDOFF_DIR=/data/handoff WORK_DIR=/data/work DB_PATH=/data/db/cad2print.sqlite \
    ORCA_BIN=/opt/orcaslicer/bin/orca-slicer

WORKDIR /app
COPY pyproject.toml README.md profiles.yaml ./
COPY src ./src
COPY scripts ./scripts
COPY tests ./tests
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -q ".[dev]" \
 && mkdir -p /data/handoff /data/work /data/db \
 && chown -R 1000:1000 /data /app

USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD /opt/venv/bin/python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"
ENTRYPOINT []
CMD ["/opt/venv/bin/uvicorn", "cad2print.app:factory", "--factory", "--host", "0.0.0.0", "--port", "8080"]
