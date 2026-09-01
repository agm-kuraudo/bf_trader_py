FROM python:3.12

# COPY paths are relative to the build context, which is the repo root (see
# docker-compose.yml `context: .`). requirements.txt lives under build/, so it
# must be referenced as build/requirements.txt here.
COPY build/requirements.txt ./

# The capture host (Raspberry Pi 500) is on a slow/flaky link, so large wheels
# (e.g. numpy) can stall and hit pip's default 15s socket timeout. Raise the
# per-read timeout and retry count so a transient stall no longer fails the
# whole build. These flags are network-hardening only; they do not change which
# packages/versions are installed.
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY . /app

WORKDIR /app