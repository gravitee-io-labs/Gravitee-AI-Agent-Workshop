FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY ./hotel-booking-a2a-agent /app

RUN pip install .

EXPOSE 8080

CMD ["a2a-agent"]
