# EasySynQ web SPA — build the static assets, serve them with `vite preview`.
# Caddy reverse-proxies `/` here and `/api`,`/healthz`,`/readyz` to the api service.
FROM node:26-slim AS build
# Refresh existing base packages before switching to the unprivileged build/runtime user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
        gzip libpcre2-8-0 libsqlite3-0 libssl3t64 openssl-provider-legacy perl-base \
    && rm -rf /var/lib/apt/lists/*
# --- non-root (audit U33) ---
# node:26-slim ships an unprivileged `node` user (uid 1000). USER is set BEFORE the install so the
# tree is created owned correctly; a `chown -R` afterwards would duplicate the whole 300 MB
# node_modules into a second layer, and these images ship in the air-gap bundle.
# `vite preview` genuinely needs write access (it uses /app/node_modules/.vite-temp), so running
# as root and chowning nothing is not an option. This image mounts no volumes, so there is nothing
# to seed and nothing an existing install has to migrate.
# Track the base's bundled package manager separately from application dependencies.
# Only the global tooling update needs root; the application install/build stays unprivileged.
# The base's setuid/setgid tools (mount, su, passwd, …) are never used by the preview server;
# clearing those bits removes the privilege-gain path under any orchestrator (#4).
# npm 11 ships stale vendored copies independently of the application lock. Replace only
# these packages; their dependency requirements match the copies npm already carries.
RUN npm install --global --ignore-scripts npm@11.19.1 \
    && mkdir -p /tmp/npm-security-patches \
    && npm pack --ignore-scripts --pack-destination /tmp/npm-security-patches \
        brace-expansion@5.0.12 undici@6.28.1 \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/brace-expansion \
        /usr/local/lib/node_modules/npm/node_modules/undici \
    && mkdir -p /usr/local/lib/node_modules/npm/node_modules/brace-expansion \
        /usr/local/lib/node_modules/npm/node_modules/undici \
    && tar -xzf /tmp/npm-security-patches/brace-expansion-5.0.12.tgz \
        -C /usr/local/lib/node_modules/npm/node_modules/brace-expansion --strip-components=1 \
    && tar -xzf /tmp/npm-security-patches/undici-6.28.1.tgz \
        -C /usr/local/lib/node_modules/npm/node_modules/undici --strip-components=1 \
    && node -e "const a = require('node:assert/strict'); const p = '/usr/local/lib/node_modules/npm/'; a.equal(require(p + 'package.json').version, '11.19.1'); a.equal(require(p + 'node_modules/brace-expansion/package.json').version, '5.0.12'); a.equal(require(p + 'node_modules/undici/package.json').version, '6.28.1');" \
    && rm -rf /tmp/npm-security-patches \
    && npm cache clean --force \
    && mkdir -p /app && chown node:node /app \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
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
