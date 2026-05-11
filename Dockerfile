FROM python:3.11-slim

# Устанавливаем сертификаты
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

# Отключаем буферизацию логов, чтобы сразу видеть ошибки в панели Render
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]