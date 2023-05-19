from abc import ABC, abstractmethod

class BetfairObject(ABC):
    @abstractmethod
    def buildFromJSON(self, json):
        pass
    
    @abstractmethod
    def buildFrameFromJSON(self, json):
        pass
