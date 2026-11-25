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
COPY services/cli/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core/admin_core /app/admin_core
COPY core/tests /app/tests
COPY core/pytest.ini /app/pytest.ini
COPY core/seed.py /app/seed.py
COPY core/seed_demo.py /app/seed_demo.py
COPY core/cli.py /app/cli.py
COPY core/migrations /app/migrations
# HTTP-слой панели нужен тестам маршрутов и CSRF. Копируем как app:
# в cli-образе этого имени ещё нет, и тесты делают ровно import app.main.
COPY services/web/app /app/app
# Релей алертов: тесты форматирования берут его по пути файла, потому что
# имя `app` уже занято веб-сервисом.
COPY services/alert-relay /app/services/alert-relay
COPY scripts /app/scripts
CMD ["python", "cli.py"]
