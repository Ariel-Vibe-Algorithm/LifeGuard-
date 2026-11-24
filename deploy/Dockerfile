# LifeGuard backend - תמונת שרת לפריסה בענן.
# בנייה (מתיקיית הפרויקט):  docker build -f deploy/Dockerfile -t lifeguard-server .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LIFEGUARD_SERVER_HOST=0.0.0.0 LIFEGUARD_SERVER_PORT=8765 \
    LIFEGUARD_SERVER_DB=/data/lifeguard_server.db LIFEGUARD_BEHIND_PROXY=1

RUN pip install --no-cache-dir cryptography==44.0.2 \
    && useradd --system --uid 10001 --home /app lifeguard \
    && mkdir -p /data && chown lifeguard /data

WORKDIR /app
COPY storage.py /app/storage.py
COPY server/ /app/server/
# הקשר לילה/תאורה לתעדוף מתנדבים (רשת העמודים: python -m research_tools.build_light_grid)
COPY context_risk.py /app/context_risk.py
COPY models/ /app/models/

USER lifeguard
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/v1/health',timeout=4).status==200 else 1)"
CMD ["python", "-m", "server.lifeguard_server"]
