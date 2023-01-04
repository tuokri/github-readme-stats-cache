#!/usr/bin/env bash

set -x

export OVERMIND_CAN_DIE=celery
export OVERMIND_AUTO_RESTART=celery

overmind start
