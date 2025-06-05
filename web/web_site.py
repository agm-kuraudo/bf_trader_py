from flask import Flask, request, render_template_string
import psycopg2
from psycopg2 import sql
import os
import logging
from dotenv import load_dotenv
app = Flask(__name__)

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)


load_dotenv()

DB_HOST = os.getenv('DB_HOST')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')

# HTML template
html_template = """
<!doctype html>
<html>
  <head><title>SQL Query Executor</title></head>
  <body>
    <h1>Run SQL Query</h1>
    <form method="post">
      <textarea name="query" rows="4" cols="60" placeholder="Enter SELECT query here">{{ query }}</textarea><br>
      <input type="submit" value="Execute">
    </form>
    {% if results %}
      <h2>Results:</h2>
      <table border="1">
        <tr>{% for col in results[0].keys() %}<th>{{ col }}</th>{% endfor %}</tr>
        {% for row in results %}
          <tr>{% for val in row.values() %}<td>{{ val }}</td>{% endfor %}</tr>
        {% endfor %}
      </table>
    {% endif %}
    {% if error %}
      <p style="color:red;">{{ error }}</p>
    {% endif %}
  </body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    results = None
    error = None
    query = ""

    if request.method == 'POST':
        query = request.form['query']
        app.logger.debug(f"Received query: {query}")

        if not query.strip().lower().startswith('select'):
            error = "Only SELECT queries are allowed."
        else:
            try:
                app.logger.debug(f"Attempting to connect with username: {DB_USER}, password: {DB_PASSWORD}")
                conn = psycopg2.connect(
                    host=DB_HOST,
                    port=DB_PORT,
                    dbname=DB_NAME,
                    user=DB_USER,
                    password=DB_PASSWORD
                )
                app.logger.debug("Connection successful.")
                cur = conn.cursor()
                cur.execute(query)
                colnames = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                results = [dict(zip(colnames, row)) for row in rows]
                cur.close()
                conn.close()
            except Exception as e:
                error = f"Database error: {str(e)}"
                app.logger.error("Database connection or query failed", exc_info=True)

    return render_template_string(html_template, results=results, error=error, query=query)

if __name__ == '__main__':
    app.run(debug=True)
