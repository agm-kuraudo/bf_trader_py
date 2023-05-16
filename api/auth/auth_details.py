import os
import traceback
import api.auth.vault.vault_reader

class AuthException(Exception):
    pass

'''Authentication Package added as per SP-36'''
class Auth():
    #Class variables
    crtfile = os.getenv("BF_CRT_FILE")
    keyfile = os.getenv("BF_KEY_FILE")
    appKey = os.getenv("BF_AppKey")

    def __init__(self):
        self.__bf_userid=None
        self.__bf_pwd=None
        self.__securityToken=None

    def get_credentials_from_vault(self):
        try:
            myVault = api.auth.vault.vault_reader.vaultReader()
            result = myVault.readSecret("bf")
            self.__bf_userid=result['data']['bf_userid']
            self.__bf_pwd=result['data']['bf_pwd']
        except Exception as f:
            #print(traceback.format_tb(e.__traceback__))
            raise AuthException("Could not load credentials from VAULT") from f

    #Defining all the getters and setters here - they don't do anything fancy at the moment, but better to have them set

    @property
    def bf_userid(self):
        return self.__bf_userid
    
    @property
    def bf_pwd(self):
        return self.__bf_pwd
    
    @property
    def securityToken(self):
        return self.__securityToken


    @bf_userid.setter
    def bf_userid(self, value):
        self.__bf_userid = value

    @bf_pwd.setter
    def bf_pwd(self, value):
        self.__bf_pwd = value

    @securityToken.setter
    def securityToken(self, value):
        self.__securityToken = value

#myAuth = Auth()
#myAuth.get_credentials_from_vault()
#print(myAuth.bf_userid)