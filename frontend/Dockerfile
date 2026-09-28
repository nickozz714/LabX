FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --silent
COPY . .
# De versie wordt in de bundel gebakken, zodat het scherm kan zeggen wélke
# build je voor je hebt. Zonder dat is "het lijkt niet veranderd" niet te
# beantwoorden zonder in devtools naar een bestandsnaam te turen.
ARG LABX_VERSION=dev
ENV VITE_LABX_VERSION=$LABX_VERSION
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
