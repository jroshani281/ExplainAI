"""
import_all_years.py  —  FINAL CORRECTED VERSION
=================================================
Imports ALL historical DTE Maharashtra CAP round data (2022, 2023, 2024)
into your existing PostgreSQL cutoffs table.

KEY FIX vs previous version:
  - 2022 uses 4-digit college codes  (e.g. "1002 - College Name")
  - 2023/2024/2025 use 5-digit codes (e.g. "01002 - College Name")
  - The old script had a 5-digit-only regex that silently SKIPPED all 2022 colleges!
  - Fixed: regex now accepts 4 OR 5 digit codes: r'^(\d{4,5})\s+-\s+(.+)'

  - 2024_round3 is now a PDF (not CSV) — handled correctly here.
  - All 9 files are PDF mode.

Place this script in the SAME folder as all your PDF files:
    C:\\Users\\HP\\Desktop\\ExplainAI\\backend\\

Run:
    pip install pdfplumber psycopg2-binary pandas
    python import_all_years.py
"""

import re
import os
import sys
import pdfplumber
import psycopg2

# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE — change only if your credentials differ
# ═══════════════════════════════════════════════════════════════════════════════
DB_CONFIG = {
    'host':     'localhost',
    'port':     5432,
    'dbname':   'explainai_db',
    'user':     'postgres',
    'password': 'postgres',
}

# ═══════════════════════════════════════════════════════════════════════════════
#  FILES TO IMPORT
#  Exact filenames as they appear in your backend folder.
#  Note: 2022_round2 has a trailing space before .pdf  →  "2022_round2 .pdf"
#        2022_round3 has "(1)" in the name             →  "2022ENGG_CAP3_CutOff (1).pdf"
# ═══════════════════════════════════════════════════════════════════════════════
FILES = [
    # filename                           year  round
    ('2022_round1.pdf',                  2022,  1),
    ('2022_round2.pdf',                 2022,  2),   # space before .pdf
    ('2022ENGG_CAP3_CutOff (1).pdf',     2022,  3),

    ('2023_round1.pdf',                  2023,  1),
    ('2023ENGG_CAP2_CutOff.pdf',         2023,  2),
    ('2023ENGG_CAP3_CutOff.pdf',         2023,  3),

    ('2024_round1.pdf',                  2024,  1),
    ('2024_round2.pdf',                  2024,  2),
    ('2024_round3.pdf',                  2024,  3),   # PDF (not CSV)
]

# ═══════════════════════════════════════════════════════════════════════════════
#  CITY EXTRACTION  (same logic as import_2025_fixed.py)
# ═══════════════════════════════════════════════════════════════════════════════

CITY_OVERRIDE = {
    'GRAMIN TECHNICAL AND MANAGEMENT CAMPUS NANDED.':                              'Nanded',
    'Rajendra Mane College of Engineering & Technology  Ambav Deorukh':            'Ratnagiri',
    "Phaltan Education Society's College of Engineering Thakurki Tal- Phaltan Dist-Satara": 'Satara',
    "Shree Siddheshwar Women's College Of Engineering Solapur.":                   'Solapur',
    'ISBM College Of Engineering Pune':                                            'Pune',
    'Pune Institute of Computer Technology':                                       'Pune',
    'Rajiv Gandhi College of Engineering Research & Technology  Chandrapur':       'Chandrapur',
    'Rajiv Gandhi College of Engineering Research & Technology Chandrapur':        'Chandrapur',
    'Sant Eknath College of Engineering':                                          'Aurangabad',
    "Svkm's Shri Bhagubhai Mafatlal Polytechnic & College of Engineering":         'Mumbai',
    'Karmayogi Institute of Technology':                                           'Solapur',
    'International Centre Of Excellence In Engineering  and Management (ICEEM)':   'Aurangabad',
    'International Centre Of Excellence In Engineering and Management (ICEEM)':    'Aurangabad',
    'Dr. V.K. Patil College of Engineering & Technology':                          'Ahmednagar',
    'K J Somaiya Institute of Technology':                                         'Mumbai',
    'Yadavrao Tasgaonkar College of Engineering & Management':                     'Karjat',
    'YASHWANTRAO BHONSALE INSTITUTE OF TECHNOLOGY':                                'Sindhudurg',
    'Sinhgad Institute of Technology':                                             'Pune',
    'Pravin Rohidas Patil College of Engineering & Technology':                    'Thane',
    'Sanjeevan Group of Institutions':                                             'Kolhapur',
    'Sanjay Ghodawat Institute':                                                   'Kolhapur',
    "Navsahyadri Education Society's Group of Institutions":                       'Pune',
    'Samarth College of Engineering and Management':                               'Pune',
    'COEP Technological University':                                               'Pune',
    'College of Engineering Pune':                                                 'Pune',
    'MKD Institute of Technology, Nadurbar':                                       'Nandurbar',
    'Rajgad Technical Campus':                                                     'Pune',
    'Devi Mahalaxmi College of Engineering and Technology':                        'Ratnagiri',
    'Mangaldeep College of Engineering':                                           'Aurangabad',
}

CITY_NORMALIZE = {
    'Navi  Mumbai': 'Navi Mumbai', 'Navimumbai': 'Navi Mumbai',
    'Chhatrapati Sambhajinagar': 'Aurangabad', 'Sambhajinagar': 'Aurangabad',
    'Bandra,Mumbai': 'Mumbai', 'Bandra': 'Mumbai', 'Kandivali': 'Mumbai',
    'Matunga': 'Mumbai', 'Andheri': 'Mumbai', 'Mulund': 'Mumbai', 'Borivali': 'Mumbai',
    'Ghansoli': 'Navi Mumbai', 'Nerul': 'Navi Mumbai', 'Nerul, Navi Mumbai': 'Navi Mumbai',
    'Lonavala': 'Pune', 'Bibwewadi': 'Pune', 'Akurdi': 'Pune', 'Pimpri': 'Pune',
    'Wakad': 'Pune', 'Bavdhan': 'Pune', 'Baner': 'Pune', 'Kondhwa': 'Pune',
    'Ambegaon': 'Pune', 'Talegaon': 'Pune', 'Wagholi': 'Pune', 'Uruli Kanchan': 'Pune',
    'Pirangut': 'Pune', 'Korti': 'Pune', 'Nanded City': 'Pune', 'Narhe (Ambegaon)': 'Pune',
    'Dist-Pune': 'Pune', 'Baramati Dist.Pune': 'Pune', 'Avasari Khurd': 'Pune',
    'Yadrav(Ichalkaranji)': 'Kolhapur', 'Dist Kolhapur': 'Kolhapur',
    'Chincholi Dist. Nashik': 'Nashik', '(Nashik)': 'Nashik',
    'Chas Dist. Ahmednagar': 'Ahmednagar', 'Dist.Ahmednagar': 'Ahmednagar',
    'Bota Sangamner': 'Ahmednagar', 'District Nanded': 'Nanded',
    'Dist Wardha': 'Wardha', 'Dist Thane': 'Thane', 'Dist.Thane': 'Thane',
    'Thane (E)': 'Thane', 'Badlapur(W)': 'Thane',
    'Khalapur Dist Raigad': 'Raigad', 'Tal Dist Dhule': 'Dhule', 'Dondaicha': 'Dhule',
    'Solapur(North)': 'Solapur', 'Kaman Dist. Palghar': 'Palghar',
    'Sindhi(Meghe)': 'Nagpur', 'Dist. Nandurbar': 'Nandurbar',
    'Nadurbar': 'Nandurbar', 'Nandurbar.': 'Nandurbar',
    'Dumbarwadi': 'Pune', '444302': 'Akola', 'Kashti Shrigondha': 'Ahmednagar',
    'Shirasgon': 'Nashik', 'Karjat': 'Raigad', '': 'Maharashtra',
}

CITY_KEYWORDS = [
    ('Navi Mumbai','Navi Mumbai'),('NaviMumbai','Navi Mumbai'),
    ('Amravati','Amravati'),('Nagpur','Nagpur'),('Pune','Pune'),
    ('Mumbai','Mumbai'),('Nashik','Nashik'),('Aurangabad','Aurangabad'),
    ('Sambhajinagar','Aurangabad'),('Kolhapur','Kolhapur'),('Solapur','Solapur'),
    ('Nanded','Nanded'),('Ahmednagar','Ahmednagar'),('Thane','Thane'),
    ('Ratnagiri','Ratnagiri'),('Satara','Satara'),('Sangli','Sangli'),
    ('Latur','Latur'),('Jalgaon','Jalgaon'),('Akola','Akola'),
    ('Washim','Washim'),('Wardha','Wardha'),('Yavatmal','Yavatmal'),
    ('Chandrapur','Chandrapur'),('Gondia','Gondia'),('Bhandara','Bhandara'),
    ('Gadchiroli','Gadchiroli'),('Dhule','Dhule'),('Nandurbar','Nandurbar'),
    ('Buldhana','Buldhana'),('Osmanabad','Osmanabad'),('Parbhani','Parbhani'),
    ('Hingoli','Hingoli'),('Beed','Beed'),('Bid','Beed'),
    ('Raigad','Raigad'),('Sindhudurg','Sindhudurg'),('Palghar','Palghar'),
    ('Baramati','Pune'),('Lonavala','Pune'),('Pimpri','Pune'),
    ('Chinchwad','Pune'),('Talegaon','Pune'),('Pirangut','Pune'),
    ('Wagholi','Pune'),('Bavdhan','Pune'),('Kondhwa','Pune'),
    ('Narhe','Pune'),('Wadgaon','Pune'),('Vadgaon','Pune'),
    ('Karvenagar','Pune'),('Bibwewadi','Pune'),('Akurdi','Pune'),
    ('Kothrud','Pune'),('Tathawade','Pune'),('Ambegaon','Pune'),
    ('Katraj','Pune'),('Hadapsar','Pune'),
    ('Kandivali','Mumbai'),('Borivali','Mumbai'),('Andheri','Mumbai'),
    ('Mulund','Mumbai'),('Matunga','Mumbai'),('Bandra','Mumbai'),
    ('Ghansoli','Navi Mumbai'),('Nerul','Navi Mumbai'),
    ('Belapur','Navi Mumbai'),('Kharghar','Navi Mumbai'),
    ('Vasai','Thane'),('Kalyan','Thane'),('Badlapur','Thane'),
    ('Ambernath','Thane'),('Dombivli','Thane'),('Ulhasnagar','Thane'),
    ('Shahada','Nandurbar'),('Shirpur','Dhule'),
    ('Ichalkaranji','Kolhapur'),('Kupwad','Sangli'),('Miraj','Sangli'),
    ('Pandharpur','Solapur'),('Barshi','Solapur'),
    ('Phaltan','Satara'),('Karad','Satara'),
    ('Kankavli','Sindhudurg'),('Kudal','Sindhudurg'),
    ('Deorukh','Ratnagiri'),('Shegaon','Buldhana'),
    ('Khamgaon','Buldhana'),('Malkapur','Buldhana'),
    ('Pusad','Yavatmal'),
]

def _keyword_city(name):
    n = name.upper()
    for kw, city in CITY_KEYWORDS:
        if kw.upper() in n:
            return city
    return 'Maharashtra'

def extract_city(college_name):
    name = str(college_name).strip()
    if name in CITY_OVERRIDE:
        return CITY_OVERRIDE[name]
    if ',' in name:
        raw = name.rsplit(',', 1)[-1].strip().rstrip('.')
        if len(raw) <= 30:
            normalized = CITY_NORMALIZE.get(raw, raw)
            if normalized and normalized != 'Maharashtra':
                return normalized
    return _keyword_city(name)

def normalize_city(raw):
    c = str(raw).strip().rstrip('.')
    return CITY_NORMALIZE.get(c, c)

def classify_type(status_str):
    s = str(status_str).strip()
    if s in ('nan', ''):           return 'Other'
    if s.startswith('Government'): return 'Government'
    if 'Deemed' in s:              return 'Deemed University'
    if s.startswith('University'): return 'University'
    if 'Autonomous' in s:          return 'Private Autonomous'
    if s.startswith('Un-Aided'):   return 'Private'
    return 'Other'


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF PARSER
#  KEY FIX: college code regex accepts 4 OR 5 digits (\d{4,5})
#  2022 PDFs use 4-digit codes ("1002 - ...") while 2023+ use 5-digit ("01002 - ...")
# ═══════════════════════════════════════════════════════════════════════════════

def parse_pdf(filepath, year, round_num):
    records = []
    current_college      = None
    current_college_code = None
    current_branch       = None
    current_status       = None

    print(f"\n  [PDF] Parsing {os.path.basename(filepath)} ...", flush=True)

    with pdfplumber.open(filepath) as pdf:
        total_pages = len(pdf.pages)
        print(f"  [PDF] {total_pages} pages", flush=True)

        for pi, page in enumerate(pdf.pages):
            try:
                text = page.extract_text()
            except Exception:
                continue
            if not text:
                continue

            lines = text.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i].strip()

                # ── College code + name ──────────────────────────────────────
                # FIXED: \d{4,5} handles both 2022 (4-digit) and 2023+ (5-digit)
                m = re.match(r'^(\d{4,5})\s+-\s+(.+)', line)
                if m:
                    current_college_code = m.group(1).strip()
                    current_college      = m.group(2).strip()
                    i += 1
                    continue

                # ── Branch code + name ───────────────────────────────────────
                m = re.match(r'^(\d{9,10}T?)\s+-\s+(.+)', line)
                if m:
                    current_branch = m.group(2).strip()
                    i += 1
                    continue

                # ── Status line ──────────────────────────────────────────────
                if line.startswith('Status:'):
                    current_status = line[7:].strip()
                    i += 1
                    continue

                # ── Category header: "Stage GOPENS GSCS ..." ────────────────
                if re.match(r'^Stage\s+[A-Z]', line):
                    cats = line.split()[1:]

                    # Handle wrapped headers (rare — categories overflow to next line)
                    while i + 1 < len(lines):
                        nxt = lines[i + 1].strip()
                        if (nxt and
                                not re.match(
                                    r'^(I{1,3}V?|IV|Legends|Maharashtra|'
                                    r'Home|Other|State|All\s)', nxt) and
                                not re.search(r'\d', nxt[:3])):
                            cats += nxt.split()
                            i += 1
                        else:
                            break

                    i += 1
                    while i < len(lines):
                        dline = lines[i].strip()
                        dm = re.match(r'^(I{1,4}|IV)\s+([\d\s]+)', dline)
                        if dm:
                            stage = dm.group(1)
                            pcts = []
                            if i + 1 < len(lines):
                                pl = lines[i + 1]
                                pcts = [float(x)
                                        for x in re.findall(r'\((\d+\.\d+)\)', pl)]
                                if pcts:
                                    i += 1

                            # Only Stage I = closing cutoff
                            if stage == 'I' and current_college and current_branch:
                                for j, cat in enumerate(cats):
                                    cat = cat.strip()
                                    if j < len(pcts) and pcts[j] > 0:
                                        records.append({
                                            'college_name':       current_college,
                                            'college_code':       current_college_code or '',
                                            'branch':             current_branch,
                                            'seat_type':          cat,
                                            'closing_percentile': pcts[j],
                                            'status_raw':         current_status or '',
                                            'year':               year,
                                            'round':              round_num,
                                        })
                            i += 1
                        else:
                            break
                    continue

                i += 1

    print(f"  [PDF] Parsed {len(records)} Stage-I records", flush=True)
    return records


# ═══════════════════════════════════════════════════════════════════════════════
#  ENRICH: add city + college_type
# ═══════════════════════════════════════════════════════════════════════════════

def enrich(records):
    for r in records:
        r['city']         = normalize_city(extract_city(r['college_name']))
        r['college_type'] = classify_type(r['status_raw'])
    return records


# ═══════════════════════════════════════════════════════════════════════════════
#  DEDUPLICATE: keep best cutoff per (college, branch, seat_type, year, round)
# ═══════════════════════════════════════════════════════════════════════════════

def deduplicate(records):
    seen = {}
    for r in sorted(records, key=lambda x: -x['closing_percentile']):
        key = (r['college_name'], r['branch'], r['seat_type'], r['year'], r['round'])
        if key not in seen:
            seen[key] = r
    return list(seen.values())


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Connect ────────────────────────────────────────────────────────────────
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()
        print("✅ Connected to PostgreSQL")
    except Exception as e:
        print(f"❌ Cannot connect: {e}")
        sys.exit(1)

    # ── Ensure columns exist ───────────────────────────────────────────────────
    cur.execute("ALTER TABLE cutoffs ADD COLUMN IF NOT EXISTS year  INTEGER DEFAULT 2024;")
    cur.execute("ALTER TABLE cutoffs ADD COLUMN IF NOT EXISTS round INTEGER DEFAULT 3;")
    conn.commit()
    print("✅ Schema columns confirmed.")

    # ── Process each file ──────────────────────────────────────────────────────
    all_records   = []
    missing_files = []

    for filename, year, round_num in FILES:
        if not os.path.exists(filename):
            print(f"\n⚠️  MISSING: {filename}  — skipping")
            missing_files.append(filename)
            continue

        try:
            recs = parse_pdf(filename, year, round_num)
            recs = enrich(recs)
            recs = deduplicate(recs)
            all_records.extend(recs)
            print(f"  ✅  {filename} → {len(recs)} records (year={year}, round={round_num})")
        except Exception as e:
            print(f"  ❌  ERROR: {filename}: {e}")
            import traceback; traceback.print_exc()

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"Total records to import: {len(all_records):,}")

    by_year = {}
    for r in all_records:
        key = (r['year'], r['round'])
        by_year[key] = by_year.get(key, 0) + 1

    print("\nBreakdown by year + round:")
    for (yr, rnd) in sorted(by_year):
        print(f"  {yr} Round {rnd}: {by_year[(yr, rnd)]:,} records")

    if missing_files:
        print(f"\n⚠️  Skipped (not found):")
        for f in missing_files:
            print(f"   - {f}")

    if not all_records:
        print("\n❌ No records to import.")
        cur.close(); conn.close()
        return

    # ── Clear old data for these years ─────────────────────────────────────────
    years_to_replace = sorted(set(r['year'] for r in all_records))
    print(f"\nClearing old data for years: {years_to_replace}")
    for yr in years_to_replace:
        cur.execute("DELETE FROM cutoffs WHERE year = %s", (yr,))
        conn.commit()
        print(f"  Cleared year {yr}")

    # ── Insert ─────────────────────────────────────────────────────────────────
    total = len(all_records)
    print(f"\nInserting {total:,} records ...")
    for idx, r in enumerate(all_records):
        cur.execute("""
            INSERT INTO cutoffs
                (college_name, college_code, branch, seat_type,
                 closing_percentile, year, round, city, college_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            r['college_name'], r.get('college_code', ''),
            r['branch'], r['seat_type'],
            float(r['closing_percentile']),
            r['year'], r['round'],
            r['city'], r['college_type'],
        ))
        if (idx + 1) % 2000 == 0:
            conn.commit()
            print(f"  Inserted {idx + 1:,} / {total:,} ...", flush=True)

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n{'='*55}")
    print(f"✅  DONE!  {total:,} records imported successfully.")
    print(f"Years now in database: {years_to_replace}")
    print("\nNext step: python verify_data.py")
    print("="*55)


if __name__ == '__main__':
    main()