FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py am_init.py apim_init.py ./

CMD ["python", "main.py"]
