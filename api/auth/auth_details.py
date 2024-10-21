import os
import traceback

from requests import Response

from output.log import Output as Log
import api.auth.vault.vault_reader


class AuthException(Exception):
    pass


'''
Authentication Package added as per SP-36
This class doesn't really do Everything associated with Authentication as the is the "call_auth" method in the call
class that deals with the actual API autehtnication.  This class stores the location of the certificates and the
AppKey (read from environment variables). It also handles (with the associated sub modules) getting information
from the vault (get_credentials_from_vault) as well as verifying that an SSO Token is valid (validate_betfair_token)
'''


class Auth:
    # Class variables
    crt_file = os.getenv("BF_CRT_FILE")
    key_file = os.getenv("BF_KEY_FILE")
    app_key = os.getenv("BF_AppKey")

    # session_token = os.getenv("BF_SessionToken")
    # Log.log_info("Auth class loaded - crt and key stuff {} {} {}".format(crt_file, key_file, app_key))

    def __init__(self):
        """
        Auth init call - No parameters required
        """
        self.__bf_userid = None
        self.__bf_pwd = None
        self.__securityToken = None

        if Auth.crt_file is None or Auth.key_file is None or Auth.app_key is None:
            raise AuthException("Environment variables no defined - check BF_CRT_FILE and BF_KEY_FILE"
                                " and BF_AppKey exist")

        Log.log_debug("Auth object instantiated")

    # @api.decorators.SimpleDecorator
    def get_credentials_from_vault(self):
        """
        get_credentials from vault: This method will retrieve the credentials from vault. It requires that vault
        is running and contains the required credentials
        """
        try:
            my_vault = api.auth.vault.vault_reader.VaultReader()
            Log.log_debug("{} created".format(my_vault))
            result = my_vault.read_secret("bf")
            Log.log_debug("Result: {}".format(result))
            self.__bf_userid = result['data']['bf_userid']
            self.__bf_pwd = result['data']['bf_pwd']

            result = my_vault.read_secret("bf_token")

            self.__securityToken = result['data']['bf_sso_token']
            Log.log_debug("bf user {}, bf pwd {}, sso token {}"
                          .format(self.__bf_userid, self.__bf_pwd, self.__securityToken))
            #Although we are adding the security token directly as a field in this class, will return it as well
            #for validating and testing purposes
            return self.__securityToken
        except Exception as f:
            Log.log_error(traceback.format_tb(f.__cause__))
            raise AuthException("Could not load credentials from VAULT") from f

    # This method will make a call to the Account API and check it works OK, and we have a valid session - returns
    # true or false
    @staticmethod
    def validate_betfair_token(response) -> bool:
        try:
            if type(response)==Response:
                json_response = response.json()
            else:
                json_response = response

            Log.log_info(json_response)
            if json_response.get("result") is not None:
                Log.log_info(json_response["result"])
                return True
            elif (json_response.get("error").get("data").get("AccountAPINGException").get("errorCode") ==
                  "INVALID_SESSION_INFORMATION"):
                Log.log_warning("Session is invalid: {}"
                                .format(json_response["error"]["data"]["AccountAPINGException"]["errorCode"]))
                return False
            else:
                Log.log_error("Unknown error when attempting to check session: {}".format(response.text))
                return False
        except Exception as f:
            Log.log_error(traceback.format_tb(f.__cause__))
            raise AuthException(f"Unexpected error when validating betfair token in {response}") from f

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
