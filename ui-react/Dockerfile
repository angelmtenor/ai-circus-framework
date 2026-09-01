FROM node:22-slim AS build
WORKDIR /app
COPY ui-react/package.json ui-react/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY ui-react/ ./

# Optional local theme overlay (see vite-plugins/demo-themes.ts) — demo/ is gitignored
# and usually absent; the bracket glob makes this COPY a no-op instead of a build
# failure when there's nothing to copy (same trick as an optional package-lock.json).
COPY demo/theme[s] /demo/themes

# Vite inlines import.meta.env.VITE_* at build time, so these must be build args
# (not container-start env vars) — see src/config.ts's docstring for the tradeoff.
ARG VITE_KEYCLOAK_ISSUER=""
ARG VITE_KEYCLOAK_CLIENT_ID=""
ARG VITE_KEYCLOAK_AUDIENCE=""
ARG VITE_DEV_MODE=""
ARG VITE_DEV_ORG_ID=""
ENV VITE_KEYCLOAK_ISSUER=$VITE_KEYCLOAK_ISSUER
ENV VITE_KEYCLOAK_CLIENT_ID=$VITE_KEYCLOAK_CLIENT_ID
ENV VITE_KEYCLOAK_AUDIENCE=$VITE_KEYCLOAK_AUDIENCE
ENV VITE_DEV_MODE=$VITE_DEV_MODE
ENV VITE_DEV_ORG_ID=$VITE_DEV_ORG_ID
RUN npm run build

FROM nginx:1.27-alpine
COPY ui-react/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
