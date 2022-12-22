FROM python:3.11-slim-bullseye

ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y \
    libsqlite3-dev  \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["sanic", "app.app", "--host=0.0.0.0", "--port=8080", "--workers=4", "--access-log"]
