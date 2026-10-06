FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
ARG BZCARD_BUILD_VERSION=dev
RUN npm run typecheck && VITE_BUILD_VERSION="$BZCARD_BUILD_VERSION" npm run build

FROM nginx:1.27-alpine

LABEL org.opencontainers.image.source="https://github.com/iz69/bzCard"

ENV UI_BASE_PATH=/bzcard/ \
    API_BASE_PATH=/bzcard-api

RUN apk add --no-cache python3
COPY --from=build /app/dist /opt/bzcard/dist
COPY runtime/generate_config.py /opt/bzcard/generate_config.py
COPY docker-entrypoint.d/40-bzcard-runtime.sh /docker-entrypoint.d/40-bzcard-runtime.sh
RUN chmod +x /docker-entrypoint.d/40-bzcard-runtime.sh

EXPOSE 5173
