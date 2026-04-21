"""
verify_data.py
==============
Run this AFTER import_all_years.py to confirm everything loaded correctly.
Shows row counts per year per round, flags any suspicious data.

Run:
    python verify_data.py
"""

import psycopg2
import sys

DB_CONFIG = {
    'host':     'localhost',
    'port':     5432,
    'dbname':   'explainai_db',
    'user':     'postgres',
    'password': 'postgres',
}

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    print("✅ Connected\n")
except Exception as e:
    print(f"❌ Cannot connect: {e}")
    sys.exit(1)

# ── 1. Row counts per year + round ────────────────────────────────────────────
print("=" * 50)
print("ROWS PER YEAR + ROUND")
print("=" * 50)
cur.execute("""
    SELECT year, round, COUNT(*) as rows
    FROM cutoffs
    WHERE year IN (2022, 2023, 2024, 2025)
    GROUP BY year, round
    ORDER BY year, round
""")
rows = cur.fetchall()
if not rows:
    print("⚠️  No data found! Import may have failed.")
else:
    current_year = None
    for year, round_num, count in rows:
        if year != current_year:
            if current_year is not None:
                print()
            print(f"  {year}:")
            current_year = year
        status = "✅" if count > 1000 else "⚠️ LOW"
        print(f"    Round {round_num}: {count:>6,} rows  {status}")

# ── 2. Total rows ─────────────────────────────────────────────────────────────
cur.execute("SELECT COUNT(*) FROM cutoffs WHERE year IN (2022,2023,2024,2025)")
total = cur.fetchone()[0]
print(f"\n  TOTAL: {total:,} rows across 2022-2025")

# ── 3. Check for missing cities ───────────────────────────────────────────────
print("\n" + "=" * 50)
print("CITY CHECK — colleges mapped to Maharashtra (should be 0 or very few)")
print("=" * 50)
cur.execute("""
    SELECT DISTINCT college_name, year
    FROM cutoffs
    WHERE city = 'Maharashtra' AND college_type = 'Government'
    ORDER BY year, college_name
    LIMIT 20
""")
rows = cur.fetchall()
if not rows:
    print("  ✅ No Government colleges stuck on Maharashtra — city mapping is clean!")
else:
    print(f"  ⚠️  {len(rows)} Government colleges still showing city=Maharashtra:")
    for name, year in rows:
        print(f"    [{year}] {name}")

# ── 4. Sample data from each year ─────────────────────────────────────────────
print("\n" + "=" * 50)
print("SAMPLE ROWS (1 per year to confirm data looks right)")
print("=" * 50)
for yr in [2022, 2023, 2024, 2025]:
    cur.execute("""
        SELECT college_name, branch, seat_type, closing_percentile, city, college_type, round
        FROM cutoffs
        WHERE year = %s
        LIMIT 1
    """, (yr,))
    row = cur.fetchone()
    if row:
        name, branch, seat, pct, city, ctype, rnd = row
        print(f"\n  {yr} Round {rnd}:")
        print(f"    College : {name}")
        print(f"    Branch  : {branch}")
        print(f"    Category: {seat}")
        print(f"    Cutoff  : {pct}")
        print(f"    City    : {city}")
        print(f"    Type    : {ctype}")
    else:
        print(f"\n  {yr}: ⚠️  No data found!")

# ── 5. Category distribution ──────────────────────────────────────────────────
print("\n" + "=" * 50)
print("TOP CATEGORIES IN DATABASE")
print("=" * 50)
cur.execute("""
    SELECT seat_type, COUNT(*) as cnt
    FROM cutoffs
    WHERE year IN (2022,2023,2024,2025)
    GROUP BY seat_type
    ORDER BY cnt DESC
    LIMIT 15
""")
for seat_type, cnt in cur.fetchall():
    print(f"  {seat_type:<25} {cnt:>6,}")

cur.close()
conn.close()
print("\n" + "=" * 50)
print("✅  Verification complete!")
print("If all counts look correct (>1000 rows per round), you are ready for ML.")
print("=" * 50)