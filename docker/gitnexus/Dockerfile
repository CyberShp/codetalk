FROM node:20-slim

# Build tools from bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

# @ladybugdb/core native addon requires GLIBCXX_3.4.31 (GCC 13+).
# Bookworm ships GCC 12 (GLIBCXX up to 3.4.30). Upgrade libstdc++6 from Trixie.
RUN echo 'deb http://deb.debian.org/debian trixie main' > /etc/apt/sources.list.d/trixie.list \
    && apt-get update \
    && apt-get install -y -t trixie libstdc++6 \
    && rm /etc/apt/sources.list.d/trixie.list \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gitnexus
RUN npm install -g gitnexus@latest

EXPOSE 7100

# Bridge HTTP mode — binds to 0.0.0.0 so container networking works
CMD ["gitnexus", "serve", "--port", "7100", "--host", "0.0.0.0"]
