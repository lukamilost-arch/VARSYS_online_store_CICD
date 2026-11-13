FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home store && mkdir /data && chown store:store /data
COPY app.py schema.sql ./
COPY templates templates/
COPY static static/
USER store
ENV STORE_DATABASE=/data/store.db
EXPOSE 8000
CMD ["sh", "-c", "flask --app app init-db --demo && exec flask --app app run --host=0.0.0.0 --port=8000"]
