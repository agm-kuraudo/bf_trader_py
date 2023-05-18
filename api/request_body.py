import api

class RequestBody():
    def __init__(self):
        self.templates = {}
        self.templates["CertAuth"] = {"username": "<USERID>", "password": "<PWD>"}
    
    #@api.decorators.SimpleDecorator
    def setTemplate(self, template_name, template_body):
        self.templates[template_name] = template_body

    #@api.decorators.SimpleDecorator
    def getTemplate(self, template_name):
        return self.templates[template_name]
    
    #@api.decorators.SimpleDecorator
    def populateTemplate(self, template_name, replace_pairs):
        new_dict={}

        for original, replacement in replace_pairs.items():
            for key,value in self.templates[template_name].items():
                if key not in new_dict or original in new_dict.get(key):
                    new_dict[key] = value.replace(original, replacement)

        return new_dict
