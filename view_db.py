import sqlite3

conn = sqlite3.connect('receipts.db')
cursor = conn.cursor()

# Fetch column names
cursor.execute("PRAGMA table_info(deposits)")
columns = [col[1] for col in cursor.fetchall()]

# Fetch rows
cursor.execute("SELECT * FROM deposits")
rows = cursor.fetchall()

print(f"\n{' | '.join(columns)}")
print("-" * 60)
for row in rows:
    print(row)

conn.close()