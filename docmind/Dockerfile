# 构建上下文必须是仓库根目录（见根 compose.yaml）：
#   docker build -f docmind/Dockerfile .
# 原因：requirements.txt 里的 `-e ../shared` 需要把 shared/ 一起带进镜像。
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# shared 先到位，pip 解析 `-e ../shared` 时才能找到它。
COPY shared /app/shared
COPY docmind/requirements.txt /app/docmind/requirements.txt

WORKDIR /app/docmind
RUN pip install --no-cache-dir -r requirements.txt

COPY docmind /app/docmind

# 上传目录。compose 会把它 bind 到宿主机，容器重建不丢文件。
RUN mkdir -p /app/docmind/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
