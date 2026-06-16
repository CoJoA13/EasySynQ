# EasySynQ web SPA — build the static assets, serve them with `vite preview`.
# Caddy reverse-proxies `/` here and `/api`,`/healthz`,`/readyz` to the api service.
FROM node:26-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npm run build

EXPOSE 5173
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
