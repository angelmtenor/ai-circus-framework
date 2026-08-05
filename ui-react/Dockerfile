FROM node:22-slim AS build
WORKDIR /app
COPY ui-react/package.json ui-react/package-lock.json ./
RUN npm ci
COPY ui-react/ ./

# Vite inlines import.meta.env.VITE_* at build time, so these must be build args
# (not container-start env vars) — see src/config.ts's docstring for the tradeoff.
ARG VITE_LOGTO_APP_ID=""
ENV VITE_LOGTO_APP_ID=$VITE_LOGTO_APP_ID
RUN npm run build

FROM nginx:1.27-alpine
COPY ui-react/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
