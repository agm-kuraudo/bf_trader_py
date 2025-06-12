#For this cript to work you need to have local environment variable "vault_unseal_keys" with three of the vault keys seperated by ;

$vaultUnsealKeys = $Env:vault_unseal_keys
Write-Output $vaultUnsealKeys

# Split the variable by the delimiter (;)
$vaultUnsealKey = $vaultUnsealKeys -split ";"

Write-Output $vaultUnsealKey[0]
Write-Output $vaultUnsealKey[1]
Write-Output $vaultUnsealKey[2]

# Define the container name or ID
$containerName = "my_vault"

# Start the container
docker start $containerName

# Wait for a few seconds to ensure the container is fully started
Start-Sleep -Seconds 5

# Define the unseal keys
$unsealKeys = @($vaultUnsealKey[0], $vaultUnsealKey[1], $vaultUnsealKey[2]) # Replace with your actual unseal keys

# Loop through each unseal key and unseal the Vault
foreach ($key in $unsealKeys) {
    docker exec -it $containerName /bin/sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault operator unseal $key"
}