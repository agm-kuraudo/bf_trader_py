import datetime
import os


class Output:
    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4

    LOG_CONSOLE = True
    LOG_FILE = True

    SELECTED_LOG_LEVEL = 1  # Defaults to debug - can be changed by calling "set_log_level"
    full_path = os.path.join(os.path.dirname(__file__), "log/runlog" + datetime.datetime.now().
                             strftime("%y%m%d") + ".log")

    def __init__(self):
        pass

    @classmethod
    def set_log_level(cls, log_level):
        cls.SELECTED_LOG_LEVEL = log_level

    @classmethod
    def log_debug(cls, msg):
        if Output.SELECTED_LOG_LEVEL <= Output.DEBUG:
            if cls.LOG_CONSOLE:
                cls.log("DEBUG:", msg)

    @classmethod
    def log_info(cls, msg):
        if Output.SELECTED_LOG_LEVEL <= Output.INFO:
            cls.log("INFO:", msg)

    @classmethod
    def log_warning(cls, msg):
        if Output.SELECTED_LOG_LEVEL <= Output.WARNING:
            cls.log("WARNING:", msg)

    @classmethod
    def log_error(cls, msg):
        if Output.SELECTED_LOG_LEVEL <= Output.ERROR:
            cls.log("ERROR:", msg)

    @classmethod
    def log(cls, level, msg):
        if cls.LOG_CONSOLE:
            cls.console_output(level + str(msg))
        if cls.LOG_FILE:
            cls.file_output(level + str(msg))

    @classmethod
    def console_output(cls, output_string):
        print(datetime.datetime.now(), output_string)

    @classmethod
    def file_output(cls, output_string):
        # stream = open(cls.full_path, mode='x', encoding="UTF-8")
        stream = open(cls.full_path, mode='a+', encoding="UTF-8")
        stream.write(str(datetime.datetime.now()) + " " + output_string + "\n")
