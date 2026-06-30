FROM node:22-alpine AS build

WORKDIR /app

ARG VITE_BASE_PATH=/bzcard/
ARG VITE_API_BASE_PATH=/bzcard-api
ARG VITE_LIFF_ID=
ARG VITE_LINE_LIFF_URL=
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
ENV VITE_API_BASE_PATH=${VITE_API_BASE_PATH}
ENV VITE_LIFF_ID=${VITE_LIFF_ID}
ENV VITE_LINE_LIFF_URL=${VITE_LINE_LIFF_URL}

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 5173
