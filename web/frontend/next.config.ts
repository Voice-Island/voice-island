import path from 'node:path';
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // The "N" development badge hides the bottom of the screen on mobile.
  devIndicators: false,
  // The web/ folder also contains the agent: we pin the frontend project root.
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;
