FROM python:3.12-slim

WORKDIR /app

# Cài đặt dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code và templates
COPY . .

# Expose port
EXPOSE 5000

# Run với gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]