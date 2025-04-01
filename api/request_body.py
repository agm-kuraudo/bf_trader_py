import json

from output.log import Output as Log
import decorators.log_attrib

class RequestBodyException(Exception):
    pass

class RequestBody:
    @decorators.log_attrib.dump_args
    def __init__(self):
        """
        RequestBody init - sets up all the template requests we need
        """
        self.__templates = \
            {
                "CertAuth":
                    {
                        "username": "<USERID>",
                        "password": "<PWD>"
                    },
                "listEventTypes":
                    {
                        "jsonrpc": "2.0",
                        "method": "SportsAPING/v1.0/listEventTypes",
                        "params":
                            {
                                "filter": {}
                            },
                        "id": 1
                    },
                "listCompetitions":
                    {
                        "jsonrpc": "2.0",
                        "method": "SportsAPING/v1.0/listCompetitions",
                        "params":
                            {
                                "filter":
                                    {
                                        "eventTypeIds": "<list_of_event_ids>"
                                    }
                            },
                        "id": 1
                    },
                "listEvents":
                    {
                        "jsonrpc": "2.0",
                        "method": "SportsAPING/v1.0/listEvents",
                        "params":
                            {
                                "filter":
                                    {
                                        "eventTypeIds": "<list_of_event_ids>",
                                        "competitionIds": "<list_of_competition_ids>",
                                        "marketTypeCodes": "<list_of_market_types>"
                                    }
                            },
                        "id": 1
                    },
                'marketCatalogue':
                    {
                        "jsonrpc": "2.0",
                        "method": "SportsAPING/v1.0/listMarketCatalogue",
                        "params":
                            {
                                "filter":
                                    {
                                        "eventIds": "<list_of_event_ids>",
                                        "marketTypeCodes": "<list_of_market_types>"
                                    },
                                "maxResults": "100",
                                "marketProjection": ["MARKET_DESCRIPTION", "RUNNER_DESCRIPTION", "RUNNER_METADATA"]
                            },
                        "id": 1
                    },
                "listMarketBook":
                    {
                        "jsonrpc": "2.0",
                        "method": "SportsAPING/v1.0/listMarketBook",
                        "params":
                            {
                                "marketIds": "<ListOfMarketIDs>",
                                "priceProjection":
                                    {
                                        "priceData": ["EX_BEST_OFFERS", "EX_ALL_OFFERS", "EX_TRADED"]
                                    }
                            },
                        "id": 1
                    },
                "listRunnerBook":
                    {
                        "jsonrpc": "2.0",
                        "method": "SportsAPING/v1.0/listRunnerBook",
                        "params":
                            {
                                "marketId": "<MarketID>",
                                "selectionId": "<RunnerID>",
                                "priceProjection":
                                    {
                                        "priceData": ["EX_BEST_OFFERS"]
                                    }
                            },
                        "id": 1
                    },
                "getAccountFunds":
                    {
                        "jsonrpc": "2.0",
                        "method": "AccountAPING/v1.0/getAccountFunds",
                        "params":
                            {
                                "wallet": "UK"
                            },
                        "id": 1
                    }
            }



#[{"jsonrpc": "2.0", "method": "SportsAPING/v1.0/listRunnerBook", "params": {"marketId":"1.239523706","selectionId":"2426","priceProjection":{"priceData":["EX_BEST_OFFERS"]}}, "id": 1}]
    @decorators.log_attrib.dump_args
    def set_template(self, template_name: str, template_body: dict) -> None:
        """
        Updates or creates a template...
        :param template_name: Sting to identify the template
        :param template_body: The body of the template itself - should be a dictionary
        """
        self.__templates[template_name] = template_body

    @decorators.log_attrib.dump_args
    def get_template(self, template_name: str) -> dict:
        """
        This is a getter for a specific template
        :param template_name:
        :return: The template value - a dictionary
        """
        return self.__templates[template_name]

    @decorators.log_attrib.dump_args
    def populate_template(self, template_name: str, replace_pairs: dict, add_quotes=False) -> dict:
        try:
            if template_name not in self.__templates:
                raise ValueError(f"Template '{template_name}' not found.")

            template = self.__templates[template_name]

            #print("Original Template:", template)

            template_str = json.dumps(template)
            #print("Template String:", template_str)  # Debugging line

            for key, value in replace_pairs.items():
                if isinstance(value, list):
                    value = json.dumps(value)
                if add_quotes:
                    template_str = template_str.replace(f'"{key}"', json.dumps(value) if not isinstance(value, str) else f'"{value}"')
                else:
                    template_str = template_str.replace(f'"{key}"', value if isinstance(value, str) else json.dumps(value))

            #print("Final Template String:", template_str)  # Debugging line

            return json.loads(template_str)
        except Exception as e:
            raise RequestBodyException(f"Unexpected error {e} whilst populating template {template_name}, replace_pairs: {replace_pairs}") from e