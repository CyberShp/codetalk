FROM node:20-slim

RUN apt-get update && apt-get install -y git python3 make g++ && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gitnexus
RUN npm install -g gitnexus@latest

EXPOSE 7100

# Bridge HTTP mode — binds to 0.0.0.0 so container networking works
CMD ["gitnexus", "serve", "--port", "7100", "--host", "0.0.0.0"]
