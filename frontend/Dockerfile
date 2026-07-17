# Multi-stage Dockerfile.
#
# Targets:
#   - dev    : runs `npm run dev` for hot-reload during local development.
#              docker-compose.yml uses this target with a bind-mount of the
#              source directory.
#   - runner : runs the production-built standalone server. Used by
#              docker-compose.prod.yml. No bind-mount, all assets are baked
#              into the image.

# ---------------------------------------------------------------------------
# Shared base — node + deps installed
# ---------------------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json* ./
RUN npm ci

# ---------------------------------------------------------------------------
# Dev target — hot reload
# ---------------------------------------------------------------------------
FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ---------------------------------------------------------------------------
# Builder — produces a standalone production build
# ---------------------------------------------------------------------------
FROM deps AS builder
COPY . .
# NEXT_PUBLIC_* is inlined into the client bundle AT BUILD TIME — passing it at
# runtime does nothing. The map page reads NEXT_PUBLIC_GOOGLE_MAPS_API_KEY, so
# it must be a build arg or the built image ships an empty key and the map shows
# its "clé absente" banner. Empty default = the rest of the app still builds.
ARG NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=""
ENV NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=$NEXT_PUBLIC_GOOGLE_MAPS_API_KEY
RUN npm run build

# ---------------------------------------------------------------------------
# Runner — minimal production image
# ---------------------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
