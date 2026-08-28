# EasySynQ web SPA — build the static assets, serve them with `vite preview`.
# Caddy reverse-proxies `/` here and `/api`,`/healthz`,`/readyz` to the api service.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY . .
# Keep install/build/removal in one layer so Playwright bytes cannot survive in a lower layer.
# This trades dependency-layer caching for artifact correctness; Vite remains for runtime preview.
RUN npm ci \
    && npm run build \
    && npm uninstall --no-save @playwright/test \
    && npm cache clean --force

# --- non-root runtime (audit U33) ---
# node:22-slim ships an unprivileged `node` user (uid 1000). This image mounts no volumes, so there
# is nothing to seed and nothing an existing install has to migrate.
RUN chown -R node:node /app
USER node

EXPOSE 5173
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
