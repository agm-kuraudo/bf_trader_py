class RequestBody():
    def __init__(self):
        self.templates = {}

    def setTemplate(self, template_name, template_body):
        self.templates[template_name] = template_body

    def getTemplate(self, template_name):
        return self.templates[template_name]
    
    def populateTemplate(self, template_name, replace_pairs):
        pass
        #TODO: For each key/value in replace_pairs update the template body, then return the full request.
