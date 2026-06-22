# Tutor frontend — a static site served by nginx.
#
# The app is pure ES6 + the vendored agent-server-client SDK; there is no
# backend. The browser talks to agent_server through the domain proxy's public
# /llm/ path (same origin), so this image only needs to serve files.
FROM nginx:alpine

# Static frontend baked at the web root. The data/ folder (question packages
# and other runtime content) is NOT copied — it is bind-mounted at /data so it
# can be edited without rebuilding the image (see docker-compose.yml). app.js
# fetches ../data/packages/ai-901-core.json relative to /js/, i.e. /data/...
COPY frontend/ /usr/share/nginx/html/

# Container nginx serves at root; the domain proxy strips the /tutor/ prefix.
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
