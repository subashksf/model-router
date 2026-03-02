import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    // Proxy /api/gateway/* → gateway service so the browser never needs CORS config
    return [
      {
        source: "/api/gateway/:path*",
        destination: `${process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8000"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
