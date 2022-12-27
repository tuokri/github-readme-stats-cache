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

# --checksum=sha256:d6a715c0810ceb39c94bf61843befebe04a83a0469b53d6af0a52e2fea4e2ab3 \
ADD https://github.com/DarthSim/overmind/releases/download/v2.3.0/overmind-v2.3.0-linux-amd64.gz \
    ./
RUN gzip -fd ./overmind-v2.3.0-linux-amd64.gz
RUN mv ./overmind-v2.3.0-linux-amd64 ./overmind
RUN chmod +x ./overmind
RUN mv ./overmind /usr/local/bin/

EXPOSE 8080

CMD ["overmind", "start"]
