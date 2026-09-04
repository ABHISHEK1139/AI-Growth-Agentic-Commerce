/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  ...(process.env.BACKEND_URL
    ? {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: `${process.env.BACKEND_URL}/api/:path*`,
            },
            {
              source: "/health",
              destination: `${process.env.BACKEND_URL}/health`,
            },
          ];
        },
      }
    : {}),
};

module.exports = nextConfig;
