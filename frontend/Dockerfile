FROM node:20-slim AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Build with placeholder URLs — replaced at container startup by docker-entrypoint.sh
ENV NEXT_PUBLIC_API_URL=__RUNTIME_API_URL__
ENV NEXT_PUBLIC_WS_URL=__RUNTIME_WS_URL__

RUN npm run build

# ─── Production stage ───
FROM node:20-slim

WORKDIR /app
ENV NODE_ENV=production

# standalone output includes only the files needed to run
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 3000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["node", "server.js"]
