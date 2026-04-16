FROM node:24-alpine AS build

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps ./apps
COPY packages ./packages

RUN npm ci

ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_CLERK_PUBLISHABLE_KEY=pk_test_REPLACE_ME
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY}

RUN npm run --workspace @eduground/web build

FROM node:24-alpine

WORKDIR /app

RUN npm install --global serve

COPY --from=build /workspace/apps/web/dist ./dist

ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "serve -s dist -l tcp://0.0.0.0:${PORT:-8080}"]
