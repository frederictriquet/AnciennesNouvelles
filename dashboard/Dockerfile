FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8766
HEALTHCHECK CMD curl -f http://localhost:8766/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
