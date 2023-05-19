import datetime


class Output:
    DEBUG=1
    INFO=2
    WARNING=3
    ERROR=4
    log_level=1
    
    def __init__(self):
        pass

    @staticmethod
    def log_debug(msg):
        if Output.log_level <= Output.DEBUG:
            print(datetime.datetime.now(), "DEBUG:", msg)

    @staticmethod
    def log_info(msg):
        if Output.log_level <= Output.INFO:
            print(datetime.datetime.now(), "INFO:", msg)

    @staticmethod
    def log_warning(msg):
        if Output.log_level <= Output.WARNING:
            print(datetime.datetime.now(), "WARNING:", msg)

    @staticmethod
    def log_error(msg):
        if Output.log_level <= Output.ERROR:
            print(datetime.datetime.now(), "ERROR:", msg)