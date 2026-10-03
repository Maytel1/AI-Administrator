FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Yekaterinburg
WORKDIR /app
COPY services/cli/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core/admin_core /app/admin_core
COPY core/tests /app/tests
COPY core/pytest.ini /app/pytest.ini
COPY core/seed.py /app/seed.py
COPY core/cli.py /app/cli.py
COPY core/migrations /app/migrations
COPY scripts /app/scripts
CMD ["python", "cli.py"]
