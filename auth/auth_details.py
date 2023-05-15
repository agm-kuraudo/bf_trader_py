from vault.vault_reader import vaultReader
import os

class AuthException(Exception):
    pass

class Auth():
    def __init__(self):
        self.__bf_userid=None
        self.__bf_pwd=None
        self.__pemfile = os.getenv("BF_PEM_LOC")

    def get_credentials_from_vault(self):
        myVault = vaultReader()
        result = myVault.readSecret("bf")
        self.__bf_userid=result['data']['bf_userid']
        self.__bf_pwd=result['data']['bf_pwd']

    @property
    def pemfile(self):
        return self.__pemfile

    @property
    def bf_userid(self):
        return self.__bf_userid
    
    @property
    def bf_pwd(self):
        return self.__bf_pwd
    
    @bf_userid.setter
    def bf_userid(self, value):
        self.__bf_userid = value

    @bf_pwd.setter
    def bf_pwd(self, value):
        self.__bf_pwd = value

    @pemfile.setter
    def pemfile(self, value):
        self.__pemfile = value

myAuth = Auth()
myAuth.get_credentials_from_vault()
print(myAuth.bf_userid)