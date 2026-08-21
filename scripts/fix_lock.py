import sys

sys.path.insert(0, ".")
from BFDriver import BFDriver
from logic.simpleStategy import FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log

BF = BFDriver(FromFileStrategy(), Log.INFO)
db = DBOutputConnection()
db.open_connection(BF.get_local_db_details())
db.db_write_log("Monitor Service: INFO: Ending run successfully")
print("Lock fixed - added ending run log entry")
