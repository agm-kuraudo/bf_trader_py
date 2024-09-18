FROM python:3

COPY /build/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV BF_AppKey=wId8CbMYLNRjCwWm
ENV BF_CRT_FILE=/app/certs/client-2048.crt
ENV BF_KEY_FILE=/app/certs/client-2048.key
ENV BF_P12_FILE=/app/certs/client-2048.p12
ENV VAULT_TOKEN=hvs.JXJV9uG42i92ZncgBBjQk3I9

CMD [ "python", "/app/test.py" ]