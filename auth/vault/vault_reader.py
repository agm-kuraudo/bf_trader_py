import hvac
import os

class vaultException(Exception):
    pass

class vaultReader():
    def __init__(self):
        self.client = hvac.Client(url='http://127.0.0.1:8200', token=os.getenv('VAULT_TOKEN') )

        if not self.client.is_authenticated():
            raise vaultException ("Failed to authenticate to vault")

    def readSecret(self, path):
        return self.client.secrets.kv.v1.read_secret(mount_point='cubbyhole', path="bf")
    
#myVault = vaultReader()

#result = myVault.readSecret("bf")

#print(result['data']['bf_pwd'])



