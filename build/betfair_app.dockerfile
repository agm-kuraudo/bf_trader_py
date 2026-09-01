FROM python:3.12

# COPY paths are relative to the build context, which is the repo root (see
# docker-compose.yml `context: .`). requirements.txt lives under build/, so it
# must be referenced as build/requirements.txt here.
COPY build/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

WORKDIR /app