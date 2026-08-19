import yaml
import os
from output.log import Output as Log
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

    # Monitor timing configuration
    UPDATE_FREQUENCY_TIERS = {
        'IN_PLAY': 5,
        'LESS_THAN_3H': 300,
        'LESS_THAN_6H': 900,
        'LESS_THAN_12H': 3600,
        'MORE_THAN_12H': 14400,
    }
    INITIAL_UPDATE_FREQUENCY = 14400
    STALE_TARGET_HOURS = 24
    MONITOR_MAX_WAIT_SECONDS = 900


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
                Log.log_info(f"Selected Strategy: {yaml_content}")
                DefaultStrategy.EVENTS = yaml_content['EVENTS']
                DefaultStrategy.COMPETITIONS = yaml_content['COMPETITIONS']
                DefaultStrategy.MAX_EVENTS = yaml_content['MAX_EVENTS']
                DefaultStrategy.MIN_DAYS_TILL_START = yaml_content['MIN_DAYS_TILL_START']
                DefaultStrategy.MAX_DAYS_TILL_START = yaml_content['MAX_DAYS_TILL_START']
                DefaultStrategy.NEWEST_FIRST = yaml_content['NEWEST_FIRST']
                # Monitor timing configuration (optional keys with safe defaults)
                DefaultStrategy.UPDATE_FREQUENCY_TIERS = yaml_content.get(
                    'UPDATE_FREQUENCY_TIERS', DefaultStrategy.UPDATE_FREQUENCY_TIERS
                )
                DefaultStrategy.INITIAL_UPDATE_FREQUENCY = yaml_content.get(
                    'INITIAL_UPDATE_FREQUENCY', DefaultStrategy.INITIAL_UPDATE_FREQUENCY
                )
                DefaultStrategy.STALE_TARGET_HOURS = yaml_content.get(
                    'STALE_TARGET_HOURS', DefaultStrategy.STALE_TARGET_HOURS
                )
                DefaultStrategy.MONITOR_MAX_WAIT_SECONDS = yaml_content.get(
                    'MONITOR_MAX_WAIT_SECONDS', DefaultStrategy.MONITOR_MAX_WAIT_SECONDS
                )
        except Exception as e:
            raise StrategyException("Cannot read strategy from file") from e


# test = FromFileStrategy()
# print(FromFileStrategy.EVENTS)
# print(FromFileStrategy.COMPETITIONS)
