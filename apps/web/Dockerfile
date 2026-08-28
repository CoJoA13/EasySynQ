# EasySynQ web SPA — build the static assets, serve them with `vite preview`.
# Caddy reverse-proxies `/` here and `/api`,`/healthz`,`/readyz` to the api service.
FROM node:25-slim AS build
# --- non-root (audit U33) ---
# node:22-slim ships an unprivileged `node` user (uid 1000). USER is set BEFORE the install so the
# tree is created owned correctly; a `chown -R` afterwards would duplicate the whole 300 MB
# node_modules into a second layer, and these images ship in the air-gap bundle.
# `vite preview` genuinely needs write access (it uses /app/node_modules/.vite-temp), so running
# as root and chowning nothing is not an option. This image mounts no volumes, so there is nothing
# to seed and nothing an existing install has to migrate.
RUN mkdir -p /app && chown node:node /app
USER node
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
COPY --chown=node:node . .
# Keep install/build/removal in one layer so Playwright bytes cannot survive in a lower layer.
# This trades dependency-layer caching for artifact correctness; Vite remains for runtime preview.
RUN npm ci \
    && npm run build \
    && npm uninstall --no-save @playwright/test \
    && npm cache clean --force

EXPOSE 5173
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
