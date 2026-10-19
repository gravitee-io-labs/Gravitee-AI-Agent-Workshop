FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY ./hotel-agent /app

RUN pip install .

EXPOSE 8080

CMD ["hotel-agent"]
