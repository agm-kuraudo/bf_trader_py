import matplotlib.pyplot as plt
import pandas as pd

from BFDriver import BFDriver
from logic.research_dataset import build_records
from logic.simpleStategy import FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log


class MarketAnalyser:
    def __init__(self, market_id, start_time=None, end_time=None):
        self.market_id = str(market_id)
        self.start_time = start_time
        self.end_time = end_time
        self.db_connection = DBOutputConnection()
        self.df = pd.DataFrame(columns=["timestamp", "runner_id", "odds_to_back", "odds_to_lay"])
        self.BF = BFDriver(FromFileStrategy(), Log.DEBUG)
        self.db_details_string = self.BF.get_local_db_details()

    def open_db_connection(self):
        try:
            self.db_connection.open_connection(self.db_details_string)
            self.db_connection.db_write_log("Analyse Service: Starting run")
        except Exception as e:
            Log.log_error(f"Failed to open database connection: {e}")
            raise

    def get_runners(self):
        # NOTE: DBOutputConnection.db_read does not accept bound parameters -- it
        # takes a single pre-built SQL string -- so this read still interpolates
        # self.market_id. market_id is set from the constructor arg (not external
        # input) and the string is quoted here, but a parameterised read would be
        # preferable; adding a params path to db_read is out of scope for SP-368
        # task 11.1 (which must not modify logic/research_dataset.py or scripts/).
        try:
            safe_market_id = self.market_id.replace("'", "''")
            runners = self.db_connection.db_read(
                f"SELECT runner_ids FROM bf.target where market_id = '{safe_market_id}';"
            )
            runners = runners[0][0]  # Extract the runner string
            split_string = runners.split("|")
            runners_list = [(part.split("-")[0], part.split("-")[1]) for part in split_string]
            Log.log_info(f"List of Runners: {runners_list}")
            for runner in runners_list:
                Log.log_debug(runner[1])
            return runners_list
        except Exception as e:
            Log.log_error(f"Failed to read runner IDs: {e}")
            raise

    def get_target_row(self):
        """Read the full Target_Row for this market in the loader's expected shape.

        Returns a ``TargetRow`` tuple matching ``logic.research_dataset.TargetRow``
        -- ``(target_id, event_id, market_id, runner_ids, start_time, status,
        last_updated, notes)`` -- for ``self.market_id``, or ``None`` when the
        market has no Target_Row in ``bf.target``. The loader uses this metadata
        to resolve Runner_Identity, Outcome_Role, and Match metadata.

        NOTE: db_read has no bound-parameter path (see get_runners); the quoted
        market_id (constructor arg, not external input) is interpolated. A
        parameterised read would be preferable but is out of scope here.

        Returns:
            The Target_Row tuple for this market, or ``None`` if absent.
        """
        try:
            safe_market_id = self.market_id.replace("'", "''")
            rows = self.db_connection.db_read(
                "SELECT target_id, event_id, market_id, runner_ids, start_time, status, last_updated, notes "
                f"FROM bf.target WHERE market_id = '{safe_market_id}';"
            )
        except Exception as e:
            Log.log_error(f"Failed to read target row: {e}")
            raise

        if not rows:
            return None
        return tuple(rows[0])

    def get_market_data(self):
        """Read this market's raw Market_Rows from ``bf.market_table``.

        Returns the ``(timestamp, market_id, runner_id, odds)`` rows for
        ``self.market_id`` (optionally restricted to the configured
        start/end Capture_Timestamp window), in the ``MarketRow`` shape the
        loader's ``build_records`` consumes. The odds blob is left unparsed: the
        single odds-parsing path lives in the loader (Req 6.4).

        NOTE: db_read has no bound-parameter path (see get_runners); the quoted
        market_id and timestamp bounds (constructor args, not external input)
        are interpolated. A parameterised read would be preferable but is out of
        scope here.

        Returns:
            A list of Market_Row tuples for this market.
        """
        safe_market_id = self.market_id.replace("'", "''")
        if self.start_time and self.end_time:
            safe_start = str(self.start_time).replace("'", "''")
            safe_end = str(self.end_time).replace("'", "''")
            query = (
                'SELECT "timestamp", market_id, runner_id, odds FROM bf.market_table '
                f"where market_id='{safe_market_id}' "
                f"AND \"timestamp\" BETWEEN '{safe_start}' AND '{safe_end}';"
            )
        else:
            query = (
                'SELECT "timestamp", market_id, runner_id, odds FROM bf.market_table '
                f"where market_id='{safe_market_id}';"
            )

        try:
            raw_data_points = self.db_connection.db_read(query)
            return raw_data_points
        except Exception as e:
            Log.log_error(f"Failed to read market data points: {e}")
            raise

    def process_data(self, raw_data_points):
        """Build the odds-over-time frame via the single loader parse path.

        Delegates all odds parsing to ``logic.research_dataset.build_records``
        (Req 6.1, 6.4): the raw Market_Rows for this one ``market_id`` are shaped
        into Dataset_Records, and each record's ``best_back`` / ``best_lay`` is
        projected into ``self.df`` as ``odds_to_back`` / ``odds_to_lay`` in
        ascending-Capture_Timestamp order (the loader already orders by
        ``(market_id, timestamp, runner_id)``). This makes the per-runner series
        equal to the loader's for the same market and runner (Req 6.2) and
        removes the former inline ``ast.literal_eval`` / index-1 ladder path.

        When the loader produces no record for this market (unreachable rows, all
        unparseable, or no captured odds), ``self.df`` is left empty and a "no
        captured odds" notice is logged rather than raising (Req 6.3).

        Args:
            raw_data_points: The Market_Row tuples read for this market.
        """
        target_row = self.get_target_row()
        targets_by_market = {self.market_id: target_row} if target_row is not None else {}

        market_rows = [tuple(row) for row in raw_data_points]
        result = build_records(market_rows, targets_by_market)

        if not result.records:
            # Req 6.3: empty result + logged notice, no exception.
            Log.log_info(f"No captured odds found for market {self.market_id}")
            return

        # Records arrive ordered by (market_id, timestamp, runner_id), so the
        # per-runner best_back/best_lay series are already ascending by
        # Capture_Timestamp (Req 6.2).
        rows = [
            {
                "timestamp": record.timestamp,
                "runner_id": record.runner_id,
                "odds_to_back": record.best_back,
                "odds_to_lay": record.best_lay,
            }
            for record in result.records
        ]
        self.df = pd.DataFrame(rows, columns=["timestamp", "runner_id", "odds_to_back", "odds_to_lay"])
        self.df["timestamp"] = pd.to_datetime(self.df["timestamp"])

    def plot_data(self):
        # No-op over an empty frame (Req 6.3): nothing to plot when the loader
        # produced no records for this market.
        if self.df.empty:
            return
        for runner_id in self.df["runner_id"].unique():
            runner_df = self.df[self.df["runner_id"] == runner_id]
            plt.figure(figsize=(10, 6))
            plt.plot(runner_df["timestamp"], runner_df["odds_to_back"], label=f"Runner {runner_id} - Back")
            plt.plot(runner_df["timestamp"], runner_df["odds_to_lay"], label=f"Runner {runner_id} - Lay")
            plt.xlabel("Timestamp")
            plt.ylabel("Odds")
            plt.title(f"Odds Over Time for Runner {runner_id}")
            plt.legend()
            plt.grid(True)
            plt.savefig(f"charts/{self.market_id.replace('.', '-')}_runner_{runner_id}_odds_over_time.png")
            plt.close()

    def run_analysis(self):
        self.open_db_connection()
        self.get_runners()
        raw_data_points = self.get_market_data()
        self.process_data(raw_data_points)
        self.plot_data()


# Example usage
if __name__ == "__main__":
    service = MarketAnalyser("1.240323372", "2025-03-14 00:00:00", "2025-03-29 00:00:00")
    service.run_analysis()
