# Tutor — static frontend (nginx) + ETL backend (FastAPI/socket.io) + docling,
# all in one image. The browser still reaches agent_server via the domain proxy's
# /llm/ path; the ETL backend is reached same-origin under /etl/ (nginx proxies
# it to the in-container uvicorn). docling runs in-image for document extraction
# (models are bind-mounted at /data/models — see docker-compose.yml).
FROM python:3.12-slim

# nginx + libraries docling/pdf processing need.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx libgl1 libglib2.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

WORKDIR /app

# Python deps first (cache layer; docling/torch are large).
COPY etl/requirements.txt /app/etl/requirements.txt
RUN pip install --no-cache-dir -r /app/etl/requirements.txt

# App code + static site.
COPY etl/ /app/etl/
COPY schema/ /app/schema/
COPY frontend/ /app/frontend/
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /app/etl/start.sh

# In-container docling; models are mounted at /data/models (not baked).
ENV ETL_DOCLING_MODE=local \
    DOCLING_MODELS=/data/models/docling/models \
    ETL_DOCLING_DEVICE=auto \
    PYTHONUNBUFFERED=1

EXPOSE 80
CMD ["/app/etl/start.sh"]
