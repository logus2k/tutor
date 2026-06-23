# Tutor app image — CODE ONLY, layered on top of the stable tutor-etl-base
# (which carries CPU torch + docling + apt deps). Rebuilds are fast: they just
# re-COPY etl/, schema/, frontend/ and the nginx conf — no pip, no torch.
#
# Build the base first (rarely): docker build -f Dockerfile.base -t tutor-etl-base:2.105.0 .
# Then `docker compose up -d --build` rebuilds just this thin layer.
ARG BASE=tutor-etl-base:2.105.0
FROM ${BASE}

WORKDIR /app

# App code + static site (the only things that change between rebuilds).
COPY etl/ /app/etl/
COPY schema/ /app/schema/
COPY frontend/ /app/frontend/
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
RUN chmod +x /app/etl/start.sh

# In-container docling on CPU; models are bind-mounted at /data/models.
ENV ETL_DOCLING_MODE=local \
    DOCLING_MODELS=/data/models/docling/models \
    ETL_DOCLING_DEVICE=cpu \
    PYTHONUNBUFFERED=1

EXPOSE 80
CMD ["/app/etl/start.sh"]
