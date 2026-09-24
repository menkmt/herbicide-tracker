/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The list and detail pages once lived under keyword paths meant for a
  // WordPress site. On their own domain those are redundant; keep the old
  // addresses working for anything that linked them.
  async redirects() {
    return [
      { source: "/herbicide-tracker", destination: "/applications", permanent: true },
      { source: "/herbicide-tracker/:county", destination: "/applications/:county", permanent: true },
      { source: "/herbicide-application/:slug", destination: "/application/:slug", permanent: true },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
        ],
      },
    ];
  },
};

export default nextConfig;
