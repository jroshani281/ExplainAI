import pandas as pd
import os

# ── Change this path if your file is in a different location ──────────────────
CSV_FILE = '2024_round3.csv'   # put this script in the same folder as the CSV
# OR use full path like: r'C:\Users\YourName\Desktop\ExplainAI\backend\2024_round3.csv'
# ──────────────────────────────────────────────────────────────────────────────

if not os.path.exists(CSV_FILE):
    print(f"ERROR: File not found → {CSV_FILE}")
    print("Make sure this script is in the same folder as 2024_round3.csv")
    print("OR update the CSV_FILE path at the top of this script")
    exit()

df = pd.read_csv(CSV_FILE)

print("=" * 60)
print("FILE:", CSV_FILE)
print("=" * 60)

print(f"\nTotal rows : {len(df)}")
print(f"Total cols : {len(df.columns)}")

print("\n--- COLUMN NAMES ---")
for i, col in enumerate(df.columns):
    print(f"  [{i}] {col}")

print("\n--- DATA TYPES ---")
for col, dtype in df.dtypes.items():
    print(f"  {col:<40} {dtype}")

print("\n--- FIRST 3 ROWS ---")
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)
pd.set_option('display.max_colwidth', 40)
print(df.head(3).to_string(index=False))

print("\n--- SAMPLE VALUES PER COLUMN ---")
for col in df.columns:
    unique_vals = df[col].dropna().unique()[:4]
    print(f"  {col:<40} {list(unique_vals)}")

print("\n--- NULL / MISSING CHECK ---")
nulls = df.isnull().sum()
for col, count in nulls.items():
    if count > 0:
        print(f"  {col:<40} {count} nulls ({count/len(df)*100:.1f}%)")
if nulls.sum() == 0:
    print("  No nulls found — clean data!")

print("\n" + "=" * 60)
print("Copy everything above and share it — ready to build import script!")
print("=" * 60)