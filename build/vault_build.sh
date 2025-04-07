docker rm -f my_keyvault
docker run --cap-add=IPC_LOCK --name my_keyvault --restart unless-stopped -e 'VAULT_LOCAL_CONFIG={"storage": {"file": {"path": "/vault/file"}}, "listener": [{"tcp": { "address": "0.0.0.0:8200", "tls_disable": true}}], "default_lease_ttl": "168h", "max_lease_ttl": "720h", "ui": true}' -e VAULT_ADDR=http://127.0.0.1:8200 -p 8200:8200 -d hashicorp/vault server
