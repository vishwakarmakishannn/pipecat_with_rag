# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# Serve stage using nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# Replace default nginx config if needed, otherwise this serves static files on port 80
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
