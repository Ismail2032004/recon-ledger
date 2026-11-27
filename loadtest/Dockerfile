FROM python:3.12-slim
WORKDIR /loadtest
RUN pip install --no-cache-dir locust==2.32.5
COPY locustfile.py .
# 200 users all start at once and hammer the API for 30 seconds
CMD ["locust", "-f", "locustfile.py", "--headless", "-u", "200", "-r", "200", "-t", "30s", "--stop-timeout", "60", "--host", "http://api:8000"]
