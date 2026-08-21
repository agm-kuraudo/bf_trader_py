from abc import ABC, abstractmethod

# BetfairObject is a Parent class of all the other Betfair classes. It was included as I logically am trying to use
# Object Orientated programming, but the value of it is pretty minimal to be honest. It specifies that each
# subclass has to provide a build_from_json and a build_frame_from_json method.  The build_from_json method creates
# an individual object based on a supplied JSON message.  build_frame_from_json will build a dataframe and/or a list
# of multiple objects returned in a JSON message


class BetfairObjectException(Exception):
    pass


class BetfairObject(ABC):
    @abstractmethod
    def build_from_json(self, json):
        pass

    @abstractmethod
    def build_frame_from_json(self, json):
        pass
