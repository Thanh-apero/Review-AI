FROM python:3.12-slim

WORKDIR /app

# Cài đặt dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code và templates
COPY . .

# Thiết lập biến môi trường mặc định
ENV PORT=5000 \
    FLASK_ENV=production \
    FLASK_DEBUG=0 \
    GITHUB_TIMEOUT=60 \
    MAX_PRS=50 \
    CACHE_TIMEOUT=3600 \
    GUNICORN_TIMEOUT=120 \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5000

# Run với gunicorn với timeout cao hơn
CMD gunicorn --bind 0.0.0.0:${PORT} \
    --workers ${GUNICORN_WORKERS} \
    --threads ${GUNICORN_THREADS} \
    --timeout ${GUNICORN_TIMEOUT} \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    app:app
