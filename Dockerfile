FROM python:3.12-slim-bullseye

ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y \
    dos2unix \
    libsqlite3-dev  \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

ARG OVERMIND_VERSION="v2.5.1"
ARG OVERMIND_URL="https://github.com/DarthSim/overmind/releases/download/${OVERMIND_VERSION}/overmind-${OVERMIND_VERSION}-linux-amd64.gz"
ARG OVERMIND_SHA256="a17159b8e97d13f3679a4e8fbc9d4747f82d5af9f6d32597b72821378b5d0b6f"
ADD ${OVERMIND_URL} ./
RUN echo "${OVERMIND_SHA256} ./overmind-${OVERMIND_VERSION}-linux-amd64.gz" \
    | sha256sum --check --status
RUN gzip -fd ./overmind-${OVERMIND_VERSION}-linux-amd64.gz
RUN mv ./overmind-${OVERMIND_VERSION}-linux-amd64 ./overmind
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
