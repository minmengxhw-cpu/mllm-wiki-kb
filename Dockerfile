# 民盟知识库 Web 只读浏览器 · 容器镜像
FROM python:3.12-slim

WORKDIR /app

# 依赖（清华源加速）
COPY webapp/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /app/requirements.txt

# 应用 + 内容（wiki 知识页 + index 元数据；不含 data/raw 原文与 sqlite）
COPY webapp /app/webapp
COPY wiki /app/wiki
COPY index /app/index

ENV PORT=8000
EXPOSE 8000
WORKDIR /app/webapp
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "2", "--timeout", "60", "app:app"]
