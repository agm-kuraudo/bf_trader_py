import psycopg2

# Database credentials
DB_HOST = '127.0.0.1'
DB_PORT = 5432
DB_NAME = 'bf_trader'
DB_USER = 'postgres'
DB_PASSWORD = 'PollyOlgaSierra12!'

try:
    print(f"Attempting to connect with username: {DB_USER}, password: {DB_PASSWORD}")
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    print("✅ Connection successful.")
    conn.close()
except Exception as e:
    print("❌ Connection failed.")
    print(f"Error: {e}")
