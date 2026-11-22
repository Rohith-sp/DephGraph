# Build context = repo root (the lockfile lives there). Runs the Next.js standalone server.
FROM node:22.23.3-alpine AS build
WORKDIR /repo
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
RUN npm ci --workspace web --include-workspace-root
COPY apps/web apps/web
ENV NEXT_STANDALONE=1 NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace web

FROM node:22.23.3-alpine
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
WORKDIR /repo
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static apps/web/.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=5s --timeout=5s --start-period=15s --retries=20 \
    CMD ["node", "-e", "fetch('http://localhost:3000/login').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "apps/web/server.js"]
