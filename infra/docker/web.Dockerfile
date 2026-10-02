# `serverDb.ts` uses the built-in `node:sqlite` module, introduced in Node 22.
# Keep the image aligned with CI so build-time route evaluation cannot import a
# module that exists locally but not in production.
FROM node:22-alpine AS base

# 1. Install dependencies
FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

# The web app is a standalone npm project, not a workspace member: `apps/web`
# carries its own package.json and package-lock.json. A committed lockfile is
# what makes `npm ci` possible at all — the previous un-pinned `npm install`
# resolved fresh on every image build, so two builds of the same commit could
# ship different dependency trees.
COPY apps/web/package.json apps/web/package-lock.json ./

RUN npm ci --no-audit --no-fund

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

# The API origin the *server* uses, which is a different value from the one above
# and cannot be a runtime environment variable. `src/middleware.ts` runs on the
# Edge runtime, where Next.js only exposes statically-referenced values, so an
# un-inlined `process.env` read is simply undefined there. The middleware gates the
# merchant console by asking the API whether there is a session, so with the value
# missing it fell back to the browser's `http://localhost:8000` - which inside this
# container is the container itself - and every merchant page redirected to
# `/login?reason=gateway_unavailable`.
#
# Empty by default so a developer building outside compose keeps the relative
# behaviour rather than inheriting an in-network name that only exists under
# compose.
ARG AGENTPAY_API_INTERNAL_URL=""
ENV AGENTPAY_API_INTERNAL_URL=$AGENTPAY_API_INTERNAL_URL

# The gateway, which decides whether this tier answers `/api/*` itself at all.
#
# Build-time for the same reason as the two above: Next.js evaluates `rewrites()`
# from next.config.js while building and bakes the result into the routes manifest,
# so a `BACKEND_URL` supplied only at container start would leave the routes
# unrewritten and every `/api/*` request would land on the local fallback instead.
#
# Setting it is what makes the composed stack coherent rather than half-and-half.
# `app/api/[...path]/route.ts` refuses to answer anything but health once a gateway
# is configured, precisely because a partial fallback produces some real requests
# and some fabricated ones. That mattered concretely for auth: this tier never sets
# a session cookie, so its `POST /api/v1/auth/session` answered "authenticated"
# with no cookie to show for it and there was no `/api/v1/auth/me` to ask. The
# merchant console could therefore never reach a signed-in state from local data,
# while the middleware - which correctly asks the API - kept sending it to
# `/login?reason=no_session`.
ARG BACKEND_URL=""
ENV BACKEND_URL=$BACKEND_URL

RUN npm run build

# 3. Production runner
#
# `next.config.js` sets `output: "standalone"`, and Next documents `next start`
# as unsupported with it (it warns at startup and prints the command to use
# instead). The standalone server is also the smaller artefact: it carries a
# pruned dependency tree rather than the whole `node_modules` from the builder
# stage, which is most of why this image is separate.
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# `standalone` deliberately omits these two; without them the app boots and then
# 404s every chunk and every static asset.
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
