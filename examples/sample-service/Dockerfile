FROM python:3.13-slim

WORKDIR /app
COPY server.py /app/server.py

EXPOSE 8080
CMD ["python", "/app/server.py", "--port", "8080", "--mode", "target"]
