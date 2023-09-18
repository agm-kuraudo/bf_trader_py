import functools
import hvac
import os
from output import Output as log

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
        log.log_debug("vaultReader Object initialised")
        log.log_debug("VAULT_TOKEN environment variables is: " + os.getenv('VAULT_TOKEN'))
        try:
            self.client = hvac.Client(url='http://127.0.0.1:8200', token=os.getenv('VAULT_TOKEN'))
            log.log_debug("Created new vault object {}".format(self.client))
        except Exception as e:
            log.log_error(e.__cause__)
            raise VaultException('Failed to connect to vault')

        log.log_debug("self.client.is_authenticated(): {}".format(self.client.is_authenticated()))
        if not self.client.is_authenticated():
            raise VaultException("Failed to authenticate to vault")

    def read_secret(self, path):
        log.log_debug("readSecret called with path: " + path)
        secret_returned = self.client.secrets.kv.v1.read_secret(mount_point='cubbyhole', path=path)
        log.log_debug(secret_returned)
        return secret_returned
