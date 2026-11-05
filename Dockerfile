FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face requires port 7860
CMD ["uvicorn", "src.smartmatch.main:app", "--host", "0.0.0.0", "--port", "7860"]