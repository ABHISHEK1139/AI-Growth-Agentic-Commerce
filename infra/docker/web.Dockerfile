FROM node:20-alpine AS base

# 1. Install dependencies
FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY apps/web/package.json ./
# No committed lockfile exists for the web app, so `npm ci` (which requires
# one) cannot run here. `npm install` resolves fresh; commit a lockfile and
# switch back to `ci` for reproducible builds.
RUN npm install

# 2. Rebuild the source code
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./

ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production

# NEXT_PUBLIC_* values are baked into the client bundle at build time: a
# runtime -e flag alone is silently ignored. Declare the build arg here and
# pass it from compose so the baked origin matches the deployment.
ARG NEXT_PUBLIC_API_BASE_URL=""
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

RUN npm run build

# 3. Production runner
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["npm", "start"]
