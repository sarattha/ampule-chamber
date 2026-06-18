FROM python:3.13-slim

WORKDIR /app
COPY server.py /app/server.py
RUN useradd --create-home --shell /usr/sbin/nologin appuser

EXPOSE 8080
USER appuser
CMD ["python", "/app/server.py", "--port", "8080", "--mode", "target"]
