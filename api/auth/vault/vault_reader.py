import functools
import hvac
import os
from output import Output as Log

'''Vault Reader requires 
+ sprint vault to be running, unsealed and listening on port 8200.
+ The environment variable VAULT_TOKEN to exist and contain the valid token
+ The betfair credentials stored in the "cubbyhole" secrets engines

To start vault: 

'''


class VaultException(Exception):
    pass


class VaultReader:
    def __init__(self):
        Log.log_debug("vaultReader Object initialised")
        Log.log_debug("VAULT_TOKEN environment variables is: " + os.getenv('VAULT_TOKEN'))
        try:
            self.client = hvac.Client(url='http://172.17.0.2:8200', token=os.getenv('VAULT_TOKEN'))
            Log.log_debug("Created new vault object {}".format(self.client))
        except Exception as e:
            Log.log_error(e.__cause__)
            raise VaultException('Failed to connect to vault')

        Log.log_debug("self.client.is_authenticated(): {}".format(self.client.is_authenticated()))
        if not self.client.is_authenticated():
            raise VaultException("Failed to authenticate to vault")

    def read_secret(self, path):
        Log.log_debug("readSecret called with path: " + path)
        secret_returned = self.client.secrets.kv.v1.read_secret(mount_point='cubbyhole', path=path)
        Log.log_debug(secret_returned)
        return secret_returned

    def update_secret(self, path, key_value_dict):
        create_response = self.client.secrets.kv.v1.create_or_update_secret(
            mount_point='cubbyhole',
            path=path,
            secret=key_value_dict
        )

        Log.log_info(create_response)
