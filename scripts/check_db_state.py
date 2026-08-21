"""Quick diagnostic script to check target and market_table state."""

from BFDriver import BFDriver
from logic.simpleStategy import FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log

BF = BFDriver(FromFileStrategy(), Log.INFO)
db = DBOutputConnection()
db.open_connection(BF.get_local_db_details())

print("=== bf.target (IDENTIFIED/OPEN) ===")
targets = db.db_read(
    "SELECT market_id, status, update_frequency, last_updated, start_time FROM bf.target WHERE status IN ('IDENTIFIED', 'OPEN') LIMIT 10;"  # noqa: E501
)
for t in targets:
    print(f"  Market: {t[0]}, Status: {t[1]}, Freq: {t[2]}s, LastUpdated: {t[3]}, StartTime: {t[4]}")

if not targets:
    print("  (no targets found)")

print("\n=== bf.target (all statuses) ===")
all_targets = db.db_read("SELECT status, COUNT(*) FROM bf.target GROUP BY status;")
for t in all_targets:
    print(f"  {t[0]}: {t[1]}")

print("\n=== bf.market_table ===")
odds = db.db_read("SELECT COUNT(*) FROM bf.market_table;")
print(f"  Total rows: {odds[0][0]}")

if odds[0][0] > 0:
    recent = db.db_read("SELECT timestamp, market_id, runner_id FROM bf.market_table ORDER BY timestamp DESC LIMIT 5;")
    print("  Recent entries:")
    for r in recent:
        print(f"    {r[0]} | Market: {r[1]} | Runner: {r[2]}")
