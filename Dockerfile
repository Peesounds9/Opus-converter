FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy the rest of the source
COPY . .

# Create data dir for cached rates
RUN mkdir -p /app/data

CMD ["python", "main.py"]
