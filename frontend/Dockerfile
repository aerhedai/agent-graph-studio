# SPEC-016: production frontend image. Static build (Vite SPA), no Node
# process at runtime -- served by nginx, which also reverse-proxies API
# calls to the backend service so the whole app is reachable from one
# public origin with zero CORS gymnastics (spec-016 §3).

FROM node:22-slim AS builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./

# VITE_API_BASE is this project's existing mechanism (frontend/src/api/
# client.ts) for pointing the frontend at a non-default backend origin --
# baked in at build time, Vite's normal env-var handling. Left empty here
# so requests are relative to whatever origin nginx serves the app from,
# which is what makes the same-origin reverse-proxy below work with zero
# frontend code changes.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
