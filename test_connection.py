
import os
from urllib.parse import urlparse, unquote
import ssl
import pg8000.dbapi

try:
    url = urlparse(os.environ["DATABASE_URL"])

    conn = pg8000.dbapi.connect(
        user=unquote(url.username),
        password=unquote(url.password),
        host=url.hostname,
        port=url.port or 26257,
        database=url.path.lstrip("/"),
        ssl_context=ssl.create_default_context(),
        timeout=15
    )

    cur = conn.cursor()
    cur.execute("SELECT current_database()")
    print("Connected database:", cur.fetchone()[0])

    cur.execute("SELECT COUNT(*) FROM tournaments")
    print("Tournament records:", cur.fetchone()[0])

    cur.close()
    conn.close()
    print("Connection successful!")

except Exception as e:
    print("Connection failed:", e)
