#!/usr/bin/env bash

hatch build
hatch dep show requirements >requirements.txt
flyctl deploy --verbose --region ams --push --now
