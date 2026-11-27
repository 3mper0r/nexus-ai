import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  experimental: {
    serverActions: {
      // Uploads go browser -> Server Action -> API. The API limit is 20 MiB; the
      // default 1 MB would reject almost every PDF. Includes multipart overhead.
      bodySizeLimit: "21mb",
    },
  },
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
