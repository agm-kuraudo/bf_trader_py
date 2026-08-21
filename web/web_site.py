import logging
import os
from datetime import datetime, timedelta

import psycopg2
import sqlparse
from dotenv import load_dotenv
from flask import Flask, render_template, request

app = Flask(__name__)

logging.basicConfig(level=logging.DEBUG)
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

connection_pool = psycopg2.pool.SimpleConnectionPool(
    1,
    10,
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    options="-c statement_timeout=5000",
)


@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    target_results = None
    market_results = None
    error = None
    query = ""

    # Default date range: from 7 days ago to today
    from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")

    if request.method == "POST":
        if "query" in request.form:
            query = request.form["query"]
            parsed = sqlparse.parse(query)
            if not parsed or parsed[0].get_type() != "SELECT":
                error = "Only SELECT queries are allowed."
            else:
                try:
                    conn = connection_pool.getconn()
                    cur = conn.cursor()
                    cur.execute(query)
                    colnames = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    results = [dict(zip(colnames, row, strict=False)) for row in rows]
                    cur.close()
                    connection_pool.putconn(conn)
                except Exception as e:
                    error = f"Database error: {str(e)}"

        elif "retrieve_targets" in request.form:
            from_date = request.form.get("from_date", from_date)
            to_date = request.form.get("to_date", to_date)
            try:
                conn = connection_pool.getconn()
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT target_id, event_id, market_id, runner_ids,
                        start_time, status, update_frequency, last_updated, notes
                    FROM bf.target
                    WHERE start_time BETWEEN %s AND %s;
                """,
                    (from_date, to_date),
                )
                colnames = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                target_results = [dict(zip(colnames, row, strict=False)) for row in rows]
                cur.close()
                connection_pool.putconn(conn)
            except Exception as e:
                error = f"Database error: {str(e)}"

        elif "retrieve_market" in request.form:
            market_id = request.form.get("market_id")
            from_date = request.form.get("from_date", from_date)
            to_date = request.form.get("to_date", to_date)
            try:
                conn = connection_pool.getconn()
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT "timestamp", market_id, runner_id, odds
                    FROM bf.market_table
                    WHERE market_id = %s AND "timestamp" BETWEEN %s AND %s;
                """,
                    (market_id, from_date, to_date),
                )
                colnames = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                market_results = [dict(zip(colnames, row, strict=False)) for row in rows]
                cur.close()
                connection_pool.putconn(conn)
            except Exception as e:
                error = f"Database error: {str(e)}"

    return render_template(
        "index.html",
        results=results,
        target_results=target_results,
        market_results=market_results,
        error=error,
        query=query,
        from_date=from_date,
        to_date=to_date,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
