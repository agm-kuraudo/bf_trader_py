from abc import ABC, abstractmethod


class BetfairObject(ABC):
    @abstractmethod
    def build_from_json(self, json):
        pass

    @abstractmethod
    def build_frame_from_json(self, json):
        pass
