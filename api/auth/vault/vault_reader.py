import hvac
import os

'''Vault Reader requires 
+ sprint vault to be running, unsealed and listening on port 8200.
+ The environment variable VAULT_TOKEN to exist and contain the valid token
+ The betfair credentials stored in the "cubbyhole" secrets engines

To start vault: 

'''
class vaultException(Exception):
    pass

class vaultReader():
    def __init__(self):
        try:
            self.client = hvac.Client(url='http://127.0.0.1:8200', token=os.getenv('VAULT_TOKEN') )
        except Exception as e:
            raise vaultException('Failed to connect to vault') from e

        if not self.client.is_authenticated():
            raise vaultException ("Failed to authenticate to vault")

    def readSecret(self, path):
        return self.client.secrets.kv.v1.read_secret(mount_point='cubbyhole', path="bf")
    
