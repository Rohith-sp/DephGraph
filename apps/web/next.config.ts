import type { NextConfig } from "next";

// Standalone output is for the Docker image only (set NEXT_STANDALONE=1 there); `next start` does not support it.
const nextConfig: NextConfig = { output: process.env.NEXT_STANDALONE ? "standalone" : undefined };

export default nextConfig;
