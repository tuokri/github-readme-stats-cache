#!/usr/bin/env bash

hatch version
hatch dep show requirements >requirements.txt
flyctl deploy --verbose --region ams --push --now
