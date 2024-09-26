from api.auth.auth_details import Auth, AuthException
from api.auth.vault.vault_reader import VaultReader, VaultException
from output import Output
import unittest


class TestBetfairApp(unittest.TestCase):

    Output.LOG_FILE=False
    Output.LOG_CONSOLE = False

    def test_vault(self):

        with self.assertRaises(VaultException):
            VaultReader("192.168.0.7:8888")

        my_vault = VaultReader()
        self.assertTrue(my_vault.client.is_authenticated(), "Vault: cannot authenticate")
        self.assertTrue(len(str(my_vault.read_secret("bf"))) > 10, "Vault: cannot read secrets")

    def test_auth(self):
        my_auth = Auth()
        self.assertIsNotNone(my_auth.get_credentials_from_vault(),
                             "Auth Class cannot retrieve sso token from vault")
        with self.assertRaises(AuthException):
            my_auth.validate_betfair_token("invalid_response")




if __name__ == '__main__':
    unittest.main()