# Hermes Watch 生产镜像：单容器 = FastAPI（API + 托管前端构建产物）
# 构建上下文 = 仓库根。构建阶段编译前端，运行阶段只装后端依赖，无 node。
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
# 依赖先行：requirements 不变时命中缓存
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist ./static
# 数据与密钥落在 /data（挂卷持久化：SQLite 库 + Fernet 主密钥）
ENV HW_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8800
CMD ["python", "run.py"]
