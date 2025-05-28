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
    FLASK_DEBUG=0

# Expose port
EXPOSE 5000

# Run với gunicorn
CMD gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 app:app
