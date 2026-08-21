import traceback

from requests import Response

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from output.log import Output as Log


class AuthException(Exception):
    pass


class Auth:
    """
    Authentication class for Betfair API access (SP-36).

    Stores certificate locations and the AppKey (read from .env via DotenvLoader).
    Handles credential retrieval and SSO token validation. The actual API authentication
    call is performed by the Call class (call_auth method).
    """

    def __init__(self, loader: DotenvLoader):
        """
        Initialise Auth with secrets from the provided DotenvLoader.

        Args:
            loader: A DotenvLoader instance for reading secrets from .env.

        Raises:
            AuthException: If BF_AppKey, BF_CRT_FILE, or BF_KEY_FILE is missing or empty.
        """
        self.__loader = loader
        self.__bf_userid = None
        self.__bf_pwd = None
        self.__securityToken = None

        try:
            self.crt_file = loader.get_secret("BF_CRT_FILE")
            self.key_file = loader.get_secret("BF_KEY_FILE")
            self.app_key = loader.get_secret("BF_AppKey")
        except ConfigurationException as e:
            raise AuthException(f"Missing required variable: {e}") from e

        Log.log_debug("Auth object instantiated")

    def get_credentials(self):
        """
        Read Betfair userid and password from .env via the loader.

        Raises:
            AuthException: If BF_USERID or BF_PWD is missing or empty.
        """
        try:
            self.__bf_userid = self.__loader.get_secret("BF_USERID")
            self.__bf_pwd = self.__loader.get_secret("BF_PWD")
        except ConfigurationException as e:
            raise AuthException(f"Missing required variable: {e}") from e

        Log.log_debug("Credentials loaded from .env")

    @staticmethod
    def validate_betfair_token(response) -> bool:
        try:
            if isinstance(response, Response):
                json_response = response.json()
            else:
                json_response = response

            Log.log_debug(json_response)
            if json_response.get("result") is not None:
                Log.log_debug(json_response["result"])
                return True
            elif (
                json_response.get("error").get("data").get("AccountAPINGException").get("errorCode")
                == "INVALID_SESSION_INFORMATION"
            ):
                Log.log_warning(
                    "Session is invalid: {}".format(
                        json_response["error"]["data"]["AccountAPINGException"]["errorCode"]
                    )
                )
                return False
            else:
                Log.log_error(f"Unknown error when attempting to check session: {response.text}")
                return False
        except Exception as f:
            Log.log_error(traceback.format_tb(f.__cause__))
            raise AuthException(f"Unexpected error when validating betfair token in {response}") from f

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
