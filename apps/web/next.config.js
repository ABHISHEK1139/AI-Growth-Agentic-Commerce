/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // The API origin used by server-side code, including `src/middleware.ts`.
  //
  // Declared here so it is inlined at build time. Next.js middleware runs on the
  // Edge runtime, which only sees statically-referenced environment values, so a
  // plain `process.env.AGENTPAY_API_INTERNAL_URL` read is `undefined` there no
  // matter what the container was started with. The middleware gates the merchant
  // console on the API's answer about the session, so an unreadable value made
  // every merchant page redirect to `/login?reason=gateway_unavailable`.
  //
  // Unset stays unset, so a build outside compose keeps the relative-proxy
  // behaviour instead of inheriting an in-network hostname.
  ...(process.env.AGENTPAY_API_INTERNAL_URL
    ? { env: { AGENTPAY_API_INTERNAL_URL: process.env.AGENTPAY_API_INTERNAL_URL } }
    : {}),
  ...(process.env.BACKEND_URL
    ? {
        // beforeFiles: the local app/api/[...path] fallback route matches
        // every /api/* URL, so an afterFiles rewrite would never fire and the
        // frontend could never reach the real gateway. beforeFiles lets an
        // explicitly configured backend win; without BACKEND_URL the local
        // fallback route still serves offline/demo traffic.
        async rewrites() {
          return {
            beforeFiles: [
              {
                source: "/api/:path*",
                destination: `${process.env.BACKEND_URL}/api/:path*`,
              },
              {
                source: "/health",
                destination: `${process.env.BACKEND_URL}/health`,
              },
            ],
          };
        },
      }
    : {}),
};

module.exports = nextConfig;
