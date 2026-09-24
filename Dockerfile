FROM node:22-alpine AS dashboard-builder

WORKDIR /dashboard
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json ./
COPY src ./src
ARG VITE_PUBLIC_DEMO=true
ENV VITE_PUBLIC_DEMO=${VITE_PUBLIC_DEMO}
RUN npm run build -- --base=./

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUB_DEMO_DIR=/tmp/chenxi-space-demo \
    PORT=7860

WORKDIR /app
COPY backend /app/backend
RUN pip install --no-cache-dir "/app/backend[web,mcp]"
COPY deploy/space_app.py /app/space_app.py
COPY --from=dashboard-builder /dashboard/dist /app/frontend/dist

EXPOSE 7860
CMD ["python", "/app/space_app.py"]
