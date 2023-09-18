import yaml
import os


class SimpleStrategy:
    EVENTS = ['Soccer']
    COMPETITIONS = ['English Premier League', 'UEFA Champions League']
    MARKET_TYPEs = ['MATCH_ODDS']
    MAX_EVENTS = 5


class FromFileStrategy(SimpleStrategy):
    # EVENTS = super().EVENTS
    # COMPETITIONS = super().COMPETITIONS
    # MARKET_TYPEs = super().MARKET_TYPEs
    # MAX_EVENTS = super().MAX_EVENTS

    def __init__(self):
        absolute_path = os.path.dirname(__file__)
        relative_path = "../config/strategy.yaml"
        full_path = os.path.join(absolute_path, relative_path)

        with open(full_path) as f:
            yaml_content = yaml.safe_load(f.read())
            print(yaml_content)
            SimpleStrategy.EVENTS = yaml_content['EVENTS']
            SimpleStrategy.COMPETITIONS = yaml_content['COMPETITIONS']
            SimpleStrategy.MAX_EVENTS = yaml_content['MAX_EVENTS']


# test = FromFileStrategy()
# print(FromFileStrategy.EVENTS)
# print(FromFileStrategy.COMPETITIONS)
