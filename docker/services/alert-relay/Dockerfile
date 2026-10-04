FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Yekaterinburg
WORKDIR /app
COPY services/alert-relay/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/alert-relay/app.py /app/app.py
# Непривилегированный пользователь: сервис только шлёт HTTP, никаких прав
# сверх этого не требуется, а под root при уязвимости он был бы лишним риском.
RUN useradd --create-home --shell /usr/sbin/nologin relay \
    && chown -R relay:relay /app
USER relay
EXPOSE 8086
CMD ["python", "/app/app.py"]
