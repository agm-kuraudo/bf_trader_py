import yaml
import os

class StrategyException(Exception):
    pass

class DefaultStrategy:
    # Filter constants
    EVENTS = ['Soccer']
    COMPETITIONS = ['English Premier League', 'UEFA Champions League']
    MARKET_TYPEs = ['MATCH_ODDS']
    MAX_EVENTS = 5
    MIN_DAYS_TILL_START = 1
    MAX_DAYS_TILL_START = 5
    NEWEST_FIRST = True


class FromFileStrategy(DefaultStrategy):
    # EVENTS = super().EVENTS
    # COMPETITIONS = super().COMPETITIONS
    # MARKET_TYPEs = super().MARKET_TYPEs
    # MAX_EVENTS = super().MAX_EVENTS

    def __init__(self):
        try:
            absolute_path = os.path.dirname(__file__)
            relative_path = "../config/strategy.yaml"
            full_path = os.path.join(absolute_path, relative_path)

            with open(full_path) as f:
                yaml_content = yaml.safe_load(f.read())
                print(yaml_content)
                DefaultStrategy.EVENTS = yaml_content['EVENTS']
                DefaultStrategy.COMPETITIONS = yaml_content['COMPETITIONS']
                DefaultStrategy.MAX_EVENTS = yaml_content['MAX_EVENTS']
                DefaultStrategy.MIN_DAYS_TILL_START = yaml_content['MIN_DAYS_TILL_START']
                DefaultStrategy.MAX_DAYS_TILL_START = yaml_content['MAX_DAYS_TILL_START']
                DefaultStrategy.NEWEST_FIRST = yaml_content['NEWEST_FIRST']
        except Exception as e:
            raise StrategyException("Cannot read strategy from file") from e


# test = FromFileStrategy()
# print(FromFileStrategy.EVENTS)
# print(FromFileStrategy.COMPETITIONS)
