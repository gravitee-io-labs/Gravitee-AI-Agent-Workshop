FROM python:3.14.4-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install .

EXPOSE 8000

CMD ["acme-hotel-api"]
