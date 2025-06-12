#!/bin/bash

jsonFilePath="$(dirname "$0")/vault_config.json"

docker rm -f my_vault
docker run --cap-add=IPC_LOCK --name my_vault --restart unless-stopped --network my_trading_network --ip 172.19.0.2 -v "${jsonFilePath}:/vault/config/local.json" -e VAULT_ADDR=http://127.0.0.1:8200 -p 8200:8200 -d hashicorp/vault server
