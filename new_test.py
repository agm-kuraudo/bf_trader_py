import psycopg2

conn = psycopg2.connect(database="bf_trader",
                        host="172.17.0.3",
                        user="postgres",
                        password="PollyOlgaSierra12!",
                        port="5432")


cursor = conn.cursor()

cursor.execute("SELECT * FROM bf.log_file")

print(cursor.fetchall())