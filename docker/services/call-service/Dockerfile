FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Yekaterinburg
WORKDIR /app
# Индекс pip переопределяется при сборке: на сетях, где pypi.org
# недоступен, сборка идёт через зеркало:
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
COPY services/call-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Общий «мозг» проекта: CRM для карточки клиента и health-сервер.
COPY core/admin_core /app/admin_core
COPY core/migrations /app/migrations
COPY services/call-service/app /app/app
EXPOSE 8080
CMD ["python", "-m", "app.main"]
