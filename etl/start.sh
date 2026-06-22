#!/bin/sh
# Start the ETL service (uvicorn) and the static frontend (nginx) in one container.
# uvicorn runs on 127.0.0.1:8099; nginx serves the frontend and reverse-proxies
# /etl/ (incl. the socket.io upgrade) to it.
set -e
cd /app
uvicorn etl.service:asgi --host 127.0.0.1 --port 8099 &
exec nginx -g 'daemon off;'
