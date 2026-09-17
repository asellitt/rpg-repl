FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "markdown>=3.5"
COPY viewer/serve.py viewer/serve.py

EXPOSE 8420
CMD ["python3", "viewer/serve.py", "--host", "0.0.0.0", "--port", "8420"]
