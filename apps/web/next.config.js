/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
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
