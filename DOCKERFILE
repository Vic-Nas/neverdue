FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    DEBUG=false

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && gunicorn app.wsgi --bind 0.0.0.0:8000 --workers 2"]