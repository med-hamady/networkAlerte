/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output minimizes the prod image: only the files actually
  // needed at runtime are copied to the runner stage.
  output: 'standalone',
}

module.exports = nextConfig
