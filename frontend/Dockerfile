FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
# public/ holds optional static assets and may be empty. Ensure it exists so
# the copy into the runtime stage cannot fail on a fresh checkout.
RUN mkdir -p public
ARG TRACKER_API_URL=http://api:8000
ENV TRACKER_API_URL=$TRACKER_API_URL
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY package.json ./
RUN useradd --create-home --uid 10002 web && chown -R web:web /app
USER web
EXPOSE 3000
CMD ["npm", "run", "start"]
