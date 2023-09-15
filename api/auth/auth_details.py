import os
import traceback
from output import Output as Log
import api.auth.vault.vault_reader


class AuthException(Exception):
    pass


'''Authentication Package added as per SP-36'''


class Auth:
    # Class variables
    crt_file = os.getenv("BF_CRT_FILE")
    key_file = os.getenv("BF_KEY_FILE")
    app_key = os.getenv("BF_AppKey")
    Log.log_info("Auth class loaded - crt and key stuff {} {} {}".format(crt_file, key_file, app_key))

    def __init__(self):
        """
        Auth init call - No parameters required
        """
        self.__bf_userid = None
        self.__bf_pwd = None
        self.__securityToken = None
        Log.log_debug("Auth object instantiated")

    # @api.decorators.SimpleDecorator
    def get_credentials_from_vault(self):
        """
        get_credentials from vault: This method will retrieve the credentials from vault. It requires that vault
        is running and contains the required credentials
        """
        try:
            my_vault = api.auth.vault.vault_reader.vaultReader()
            Log.log_debug("{} created".format(my_vault))
            result = my_vault.readSecret("bf")
            Log.log_debug("Result: {}".format(result))
            self.__bf_userid = result['data']['bf_userid']
            self.__bf_pwd = result['data']['bf_pwd']
            Log.log_debug("bf user {}, bf pwd {}".format(self.__bf_userid, self.__bf_pwd))
        except Exception as f:
            Log.log_error(traceback.format_tb(f.__traceback__))
            raise AuthException("Could not load credentials from VAULT") from f

    # Defining all the getters and setters here - they don't do anything fancy at the moment, but better to have them
    # set

    @property
    def bf_userid(self):
        return self.__bf_userid

    @property
    def bf_pwd(self):
        return self.__bf_pwd

    @property
    def security_token(self):
        return self.__securityToken

    @bf_userid.setter
    def bf_userid(self, value):
        self.__bf_userid = value

    @bf_pwd.setter
    def bf_pwd(self, value):
        self.__bf_pwd = value

    @security_token.setter
    def security_token(self, value):
        self.__securityToken = value

# myAuth = Auth()
# myAuth.get_credentials_from_vault()
# print(myAuth.bf_userid)
