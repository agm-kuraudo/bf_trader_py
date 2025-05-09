FROM python:3.12

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV BF_AppKey=wId8CbMYLNRjCwWm
ENV BF_CRT_FILE=/app/certs/client-2048.crt
ENV BF_KEY_FILE=/app/certs/client-2048.key
ENV BF_P12_FILE=/app/certs/client-2048.p12
ENV VAULT_TOKEN=hvs.XoQOkFxtqN5X7BdUQg0NGP6n
