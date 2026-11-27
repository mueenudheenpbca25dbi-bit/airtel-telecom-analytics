import sqlite3

con = sqlite3.connect("data/raw/airtel.db")
for (t,) in con.execute("select name from sqlite_master where type in ('table','view')"):
    n = con.execute(f"select count(*) from {t}").fetchone()[0]
    print(f"\n### {t} ({n:,} rows)")
    cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
    print("columns:", cols)
    print("sample :", con.execute(f"select * from {t} limit 1").fetchall())