/**
 * The browser only ever talks to its own origin. `/api/*` is rewritten to the FastAPI
 * process so the Session cookie stays first-party, CSRF `Origin` checks see a same-origin
 * request, and no CORS grant is needed in production.
 */
const apiOrigin = process.env.CLIPAH_API_ORIGIN ?? 'http://127.0.0.1:8000'

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  images: {
    unoptimized: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${apiOrigin}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
