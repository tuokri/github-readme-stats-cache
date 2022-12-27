FROM python:3.11-slim-bullseye

ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y \
    libsqlite3-dev  \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .

ADD https://github.com/DarthSim/overmind/releases/download/v2.3.0/overmind-v2.3.0-linux-amd64.gz \
 overmind-v2.3.0-linux-amd64.gz
RUN chmod +x overmind-v2.3.0-linux-amd64/overmind-v2.3.0-linux-amd64
RUN mv overmind-v2.3.0-linux-amd64/overmind-v2.3.0-linux-amd64 /usr/local/bin/
RUN ln -s /usr/local/bin/overmind-v2.3.0-linux-amd64 /usr/bin/overmind

EXPOSE 8080

CMD ["overmind", "start"]
