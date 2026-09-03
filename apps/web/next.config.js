/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
      {
        source: "/.well-known/:path*",
        destination: "http://127.0.0.1:8000/.well-known/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
