from typing import Dict, Any, List

import api
from output import Output as Log
import decorators.log_attrib

class RequestBodyException(Exception):
    pass

class RequestBody:
    @decorators.log_attrib.dump_args
    def __init__(self):
        """
        RequestBody init - sets up all the template requests we need
        """
        self.templates = {}
        self.templates["CertAuth"] = {"username": "<USERID>", "password": "<PWD>"}
        self.templates["listEventTypes"] = {"jsonrpc": "2.0", "method": "SportsAPING/v1.0/listEventTypes",
                                            "params": {"filter": {}}, "id": 1}

        self.templates["listCompetitions"] = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listCompetitions",
            "params": {
                "filter": {
                    "eventTypeIds": "<list_of_event_ids>"
                }
            },
            "id": 1
        }

        self.templates["listEvents"] = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listEvents",
            "params": {
                "filter": {
                    "eventTypeIds": "<list_of_event_ids>",
                    "competitionIds": "<list_of_competition_ids>",
                    "marketTypeCodes": "<list_of_market_types>"
                }
            },
            "id": 1
        }

        self.templates['marketCatalogue'] = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketCatalogue",
            "params": {
                "filter": {
                    "eventIds": "<list_of_event_ids>",
                    "marketTypeCodes": "<list_of_market_types>"
                },
                "maxResults": "100",
                "marketProjection": ["MARKET_DESCRIPTION", "RUNNER_DESCRIPTION", "RUNNER_METADATA"]
            },
            "id": 1
        }

        self.templates["listMarketBook"] = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": "<ListOfMarketIDs>",
                "priceProjection": {
                    "priceData": [
                        "EX_BEST_OFFERS",
                        "EX_ALL_OFFERS",
                        "EX_TRADED"
                    ]
                }
            },
            "id": 1
        }

        self.templates["getAccountFunds"] = {
            "jsonrpc": "2.0",
            "method": "AccountAPING/v1.0/getAccountFunds",
            "params": {
                "wallet": "UK"
            },
            "id": 1
            }

    @decorators.log_attrib.dump_args
    def set_template(self, template_name: str, template_body: dict) -> None:
        """
        Updates or creates a template... TODO: this should just be a standard "setter"
        :param template_name: Sting to identify the template
        :param template_body: The body of the template itself - should be a dictionary
        """
        self.templates[template_name] = template_body

    @decorators.log_attrib.dump_args
    def get_template(self, template_name: str) -> dict:
        """
        This is a getter for a specific template
        :param template_name:
        :return: The template value - a dictionary
        """
        return self.templates[template_name]

    @decorators.log_attrib.dump_args
    def populate_template(self, template_name: str, replace_pairs: dict, inner_dict: dict = None) -> dict:
        """
        take the template value specified in the template name param (or use inline inner_dict argument) and replace
        the key value pairs specified by replace_pair. Return the updated dictionary
        :param template_name:
        :param replace_pairs:
        :param inner_dict:
        :return:
        """
        try:
            new_dict = {}

            if inner_dict is None:
                loop_dict = self.templates[template_name]
            else:
                loop_dict = inner_dict

            for original, replacement in replace_pairs.items():
                Log.log_debug("Original: {}, Replacement: {}".format(original, replacement))
                for key, value in loop_dict.items():
                    Log.log_debug("key: {}, value: {}".format(key, value))

                    if type(value) == dict:
                        Log.log_debug("Nested Dictionary in this request: {}".format(value))
                        new_dict[key] = self.populate_template(None, replace_pairs, value)
                    if key not in new_dict or original in str(new_dict.get(key)):
                        Log.log_debug("IF Statement TRUE {}".format(loop_dict[key]))
                        if type(replacement) == str:
                            new_dict[key] = value.replace(original, replacement)
                            Log.log_debug("Making Replacements {} : {}".format(original, replacement))
                        elif loop_dict[key] == original:
                            new_dict[key] = replacement
                        else:
                            new_dict[key] = value

            return new_dict
        except Exception as e:
            raise RequestBodyException("Unexpected error whilst populating template") from e
