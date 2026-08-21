# nginxinc/nginx-unprivileged, not the plain nginx:1.27-alpine image -- the
# official image runs as root by default (needed to bind port 80); this one
# is the same nginx build, already configured to run as an unprivileged user
# listening on 8080 instead, so no non-root plumbing has to be hand-rolled
# here the way the Python services do it with their own `USER appuser`.
FROM nginxinc/nginx-unprivileged:1.27-alpine

# Static files only — no build step, no framework, deliberately dependency-free.
COPY index.html style.css app.js config.js favicon.ico /usr/share/nginx/html/

# Default nginx.conf already does SPA-friendly static serving well enough for
# a single flat index.html with no client-side routing; no custom conf needed.

EXPOSE 8080
