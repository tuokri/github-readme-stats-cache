FROM python:3.11-slim-bullseye

ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y \
    dos2unix \
    libsqlite3-dev  \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# --checksum=sha256:d6a715c0810ceb39c94bf61843befebe04a83a0469b53d6af0a52e2fea4e2ab3 \
ADD https://github.com/DarthSim/overmind/releases/download/v2.3.0/overmind-v2.3.0-linux-amd64.gz \
    ./
RUN gzip -fd ./overmind-v2.3.0-linux-amd64.gz
RUN mv ./overmind-v2.3.0-linux-amd64 ./overmind
RUN chmod +x ./overmind
RUN mv ./overmind /usr/local/bin/

EXPOSE 8080

RUN useradd --gid root --create-home --system \
    --shell /bin/bash overmind_user

RUN chown -R overmind_user /home/overmind_user/

USER overmind_user
WORKDIR /home/overmind_user/

COPY --chown=overmind_user:overmind_user ./ /home/overmind_user/

RUN dos2unix ./*

ENTRYPOINT ["bash", "run.sh"]
