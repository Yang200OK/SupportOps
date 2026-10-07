FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json /app/
RUN npm ci
RUN npx playwright install --with-deps chromium
COPY frontend /app/frontend
COPY examples /app/examples
# 依赖来自本镜像的 npm ci；保持项目原相对目录，供静态示例导入使用。
RUN mv /app/node_modules /app/frontend/node_modules
WORKDIR /app/frontend
CMD ["npm", "exec", "vite", "--", "--config", "vite.reproduction.config.ts"]
