import functools
import hvac
import os
from output import Output as Log

'''Vault Reader requires 
+ Vault to be running, unsealed and listening on port 8200.
+ The environment variable VAULT_TOKEN to exist and contain the valid token
+ The betfair credentials stored in the "cubbyhole" secrets engines
+ The correct vault_url supplied in the init section
+ hvac module installed

For instructions on starting Vault see confluence page: SP-35+-+Vault+for+secrets
'''


class VaultException(Exception):
    pass

#VaultReader uses hvac module to access the required information from vault.
class VaultReader:
    # VaultReader init has optional URL parameter if the default isn't sufficient. It will create the connection
    # to vault straight away and raise VaultException if it doesn't work
    def __init__(self, vault_url="http://172.17.0.2:8200"):
        Log.log_debug("vaultReader Object initialised")
        Log.log_debug("VAULT_TOKEN environment variables is: " + os.getenv('VAULT_TOKEN'))
        try:
            self.client = hvac.Client(url=vault_url, token=os.getenv('VAULT_TOKEN'))
            Log.log_debug("Created new vault object {}".format(self.client))
        except Exception as e:
            Log.log_error(e.__cause__)
            raise VaultException('Failed to connect to vault')

        Log.log_debug("self.client.is_authenticated(): {}".format(self.client.is_authenticated()))
        if not self.client.is_authenticated():
            raise VaultException("Failed to authenticate to vault")

    #read_secret method will attempt to read the secret at the specified path
    def read_secret(self, path):
        Log.log_debug("readSecret called with path: " + path)
        secret_returned = self.client.secrets.kv.v1.read_secret(mount_point='cubbyhole', path=path)
        Log.log_debug(secret_returned)
        return secret_returned

    #update secret will update a secret on the specified path
    def update_secret(self, path, key_value_dict):
        create_response = self.client.secrets.kv.v1.create_or_update_secret(
            mount_point='cubbyhole',
            path=path,
            secret=key_value_dict
        )

        Log.log_info(create_response)
