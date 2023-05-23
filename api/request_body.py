import api
from output import Output as log

class RequestBody():
    def __init__(self):
        self.templates = {}
        self.templates["CertAuth"] = {"username": "<USERID>", "password": "<PWD>"}
        self.templates["listEventTypes"] = {"jsonrpc": "2.0", "method": "SportsAPING/v1.0/listEventTypes", "params": {"filter":{ }}, "id": 1}

        self.templates["listCompetitions"] = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listCompetitions",
            "params": {
                "filter": {
                "eventTypeIds": "<ListOfEventIDs>"
                }
            },
            "id": 1
            }
    
        self.templates["listEvents"] = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listEvents",
            "params": {
                "filter": {
                "eventTypeIds": "<ListOfEventIDs>",
                "competitionIds": "<ListOfcompetitionIds>",
                "marketTypeCodes": "<ListOfmarketType>"
                }
            },
            "id": 1
            }


    #@api.decorators.SimpleDecorator
    def setTemplate(self, template_name, template_body):
        self.templates[template_name] = template_body

    #@api.decorators.SimpleDecorator
    def getTemplate(self, template_name):
        return self.templates[template_name]
    
    #@api.decorators.SimpleDecorator
    def populateTemplate(self, template_name, replace_pairs, inner_dict=None):
        new_dict={}

        if inner_dict==None:
            loop_dict=self.templates[template_name]
        else:
            loop_dict=inner_dict

        for original, replacement in replace_pairs.items():
            log.log_debug("Original: {}, Replacement: {}".format(original, replacement))
            for key,value in loop_dict.items():
                log.log_debug("key: {}, value: {}".format(key, value))

                if type(value) == dict:
                    log.log_warning("Nested Dictionary in this request: {}".format(value))
                    new_dict[key] = self.populateTemplate(None, replace_pairs, value)
                if key not in new_dict or original in str(new_dict.get(key)):
                    log.log_debug("IF Statement TRUE {}".format(loop_dict[key]))
                    if type(replacement) == str:
                        new_dict[key] = value.replace(original, replacement)
                        log.log_info("Making Replacements {} : {}".format(original, replacement))
                    elif loop_dict[key] == original:
                        new_dict[key] = replacement
                    else:
                        new_dict[key] = value

        return new_dict
