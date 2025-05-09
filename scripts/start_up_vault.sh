#!/bin/bash

# Load variables from /etc/environment
if [ -f /etc/environment ]; then
    set -o allexport
    source /etc/environment
    set +o allexport
else
    echo "Error: /etc/environment file not found."
    exit 1
fi

# Check if the environment variable is set
if [ -z "$vault_unseal_keys" ]; then
    echo "Error: vault_unseal_keys environment variable is not set."
    exit 1
fi

# Split the variable by the delimiter (;)
IFS=';' read -r -a vaultUnsealKey <<< "$vault_unseal_keys"

# Check if we have exactly three keys
if [ ${#vaultUnsealKey[@]} -ne 3 ]; then
    echo "Error: Expected 3 unseal keys, but got ${#vaultUnsealKey[@]}."
    exit 1
fi

# Define the container name or ID
containerName="my_vault"

# Start the container
docker start $containerName

# Wait for a few seconds to ensure the container is fully started
sleep 5

# Define the unseal keys
unsealKeys=("${vaultUnsealKey[0]}" "${vaultUnsealKey[1]}" "${vaultUnsealKey[2]}")

# Loop through each unseal key and unseal the Vault
for key in "${unsealKeys[@]}"; do
    docker exec -i $containerName /bin/sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault operator unseal $key"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to unseal Vault with key $key."
        exit 1
    fi
done

echo "Vault unsealed successfully."
