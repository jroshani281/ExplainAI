"""
patch_app_routes.py
===================
Replaces the two prediction/trend routes in app.py with fixed versions.

FIXES applied:
  1. /api/predict/2026
     - trend now shows ROUND-1 cutoff per year (not final-round).
       Students see the Round 1 history, matching what they experience.
     - trend_direction computed from R1 values across years (not final round).
     - confidence label maps: High=8+ data_points, Medium=4+, Low=<4
       (matches new predict_2026.py thresholds).

  2. /api/trend/rounds
     - No logic changes needed here (it was already correct).
     - Added round_count per year to the response so the frontend can show
       how many rounds of data exist for each year.
     - Added per-year R1 cutoff explicitly so frontend can chart R1-only trend.

Run from your backend folder:
    python patch_app_routes.py
"""

import os
import re

APP_PATH = 'app.py'

# ── Replacement routes ─────────────────────────────────────────────────────────
REPLACEMENT = '''
# ── Cutoff Predictions Model ───────────────────────────────────────────────────

class CutoffPrediction(db.Model):
    __tablename__ = \'cutoff_predictions\'
    id                   = db.Column(db.Integer, primary_key=True)
    college_name         = db.Column(db.String(200))
    college_code         = db.Column(db.String(20))
    branch               = db.Column(db.String(300))
    seat_type            = db.Column(db.String(30))
    city                 = db.Column(db.String(100))
    college_type         = db.Column(db.String(100))
    predicted_year       = db.Column(db.Integer)
    predicted_percentile = db.Column(db.Float)
    confidence           = db.Column(db.String(10))
    data_points          = db.Column(db.Integer)


# ── Prediction API ─────────────────────────────────────────────────────────────

@app.route(\'/api/predict/2026\', methods=[\'GET\'])
def predict_2026():
    """
    GET /api/predict/2026?college=COEP&branch=Computer&seat_type=GOPENS

    Returns predicted 2026 cutoff + full historical trend for the modal.

    FIXED:
    - Historical trend now uses ROUND-1 cutoff per year so students see
      the same round they are applying in (Round 1 card).
    - trend_direction is computed from R1-only values across years.
    - confidence label thresholds match new predict_2026.py (8/4 pts).
    - data_points reflects ALL rounds × ALL years (up to ~12).
    """
    college   = request.args.get(\'college\', \'\').strip()
    branch    = request.args.get(\'branch\', \'\').strip()
    seat_type = request.args.get(\'seat_type\', \'\').strip()

    if not college or not branch or not seat_type:
        return jsonify({\'status\': \'error\',
                        \'error\': \'college, branch, seat_type are required\'}), 400

    # ── Fetch prediction from DB ───────────────────────────────────────────────
    pred = CutoffPrediction.query.filter(
        CutoffPrediction.college_name.ilike(f\'%{college}%\'),
        CutoffPrediction.branch.ilike(f\'%{branch}%\'),
        CutoffPrediction.seat_type == seat_type,
        CutoffPrediction.predicted_year == 2026
    ).first()

    # ── Fetch all historical rows for this combo (all rounds, all years) ──────
    history = Cutoff.query.filter(
        Cutoff.college_name.ilike(f\'%{college}%\'),
        Cutoff.branch.ilike(f\'%{branch}%\'),
        Cutoff.seat_type == seat_type,
        Cutoff.year.between(2022, 2025)
    ).order_by(Cutoff.year, Cutoff.round).all()

    # ── Build per-year structure ───────────────────────────────────────────────
    # yearly_rounds[year][round] = closing_percentile
    yearly_rounds = {}
    for row in history:
        yr = row.year
        rnd = row.round
        if yr not in yearly_rounds:
            yearly_rounds[yr] = {}
        yearly_rounds[yr][rnd] = round(row.closing_percentile, 2)

    # For the trend chart: use Round-1 cutoff per year.
    # If a year has no R1 data, fall back to the lowest available round.
    trend_data = []
    for yr in sorted(yearly_rounds.keys()):
        rounds = yearly_rounds[yr]
        if not rounds:
            continue
        r1_val = rounds.get(1) or rounds.get(min(rounds.keys()))
        final_val = rounds.get(max(rounds.keys()))
        trend_data.append({
            \'year\':        yr,
            \'cutoff\':      r1_val,          # R1 cutoff — what this round shows
            \'final_cutoff\': final_val,      # final round cutoff (extra context)
            \'rounds\':      rounds,          # all rounds for the round-by-round table
        })

    # ── Trend direction (from R1 values year-over-year) ───────────────────────
    trend_direction = \'stable\'
    r1_values = [d[\'cutoff\'] for d in trend_data if d[\'cutoff\'] is not None]
    if len(r1_values) >= 2:
        # Use slope of last 2 R1 values
        recent_change = r1_values[-1] - r1_values[-2]
        if recent_change > 1.0:
            trend_direction = \'rising\'
        elif recent_change < -1.0:
            trend_direction = \'falling\'

    # ── Confidence label (human-readable) ─────────────────────────────────────
    # Thresholds: High=8+ data points, Medium=4+, Low<4
    # data_points in DB now counts all rounds × all years (fixed in predict_2026.py)
    def confidence_label(dp):
        if dp is None:
            return \'Low\'
        if dp >= 8:
            return \'High\'
        if dp >= 4:
            return \'Medium\'
        return \'Low\'

    if not pred:
        return jsonify({
            \'status\':          \'no_prediction\',
            \'message\':         \'No 2026 prediction available — run predict_2026.py first\',
            \'trend\':           [{\'year\': d[\'year\'], \'cutoff\': d[\'cutoff\'],
                                  \'rounds\': d[\'rounds\']} for d in trend_data],
            \'trend_direction\': trend_direction,
            \'years_of_data\':   len(trend_data),
        })

    return jsonify({
        \'status\':           \'success\',
        \'college_name\':     pred.college_name,
        \'branch\':           pred.branch,
        \'seat_type\':        pred.seat_type,
        \'predicted_2026\':   round(pred.predicted_percentile, 2),
        \'confidence\':       confidence_label(pred.data_points),
        \'data_points\':      pred.data_points,
        \'years_of_data\':    len(trend_data),
        \'trend_direction\':  trend_direction,
        # Trend array: historical R1 cutoffs + 2026 prediction
        \'trend\': [
            {\'year\': d[\'year\'], \'cutoff\': d[\'cutoff\'],
             \'final_cutoff\': d[\'final_cutoff\'], \'rounds\': d[\'rounds\']}
            for d in trend_data
        ] + [
            {\'year\': 2026, \'cutoff\': round(pred.predicted_percentile, 2),
             \'predicted\': True}
        ],
    })


# ── Round-to-Round Trend API ───────────────────────────────────────────────────

@app.route(\'/api/trend/rounds\', methods=[\'GET\'])
def round_trend():
    """
    GET /api/trend/rounds?college=COEP&branch=Computer&seat_type=GOPENS

    Shows how cutoff moves across R1→R2→R3→R4 within each year, PLUS
    the year-over-year R1 trend.

    FIXED / ENHANCED:
    - Returns round_count per year (how many rounds of data we have).
    - Returns r1_trend array: R1 cutoff per year — lets frontend chart
      same-round year-over-year movement cleanly.
    - avg_r1_to_final_drop is now the mean of all available years,
      not just the most recent year.
    """
    college   = request.args.get(\'college\', \'\').strip()
    branch    = request.args.get(\'branch\', \'\').strip()
    seat_type = request.args.get(\'seat_type\', \'GOPENS\').strip()

    rows = Cutoff.query.filter(
        Cutoff.college_name.ilike(f\'%{college}%\'),
        Cutoff.branch.ilike(f\'%{branch}%\'),
        Cutoff.seat_type == seat_type,
        Cutoff.year.between(2022, 2025)
    ).order_by(Cutoff.year, Cutoff.round).all()

    # Build data[year][round] = cutoff
    data = {}
    for r in rows:
        yr = r.year
        if yr not in data:
            data[yr] = {}
        data[yr][r.round] = round(r.closing_percentile, 2)

    result = []
    r1_to_final_drops = []
    r1_trend = []          # NEW: R1 cutoff per year for year-over-year chart

    for yr in sorted(data.keys()):
        rounds = data[yr]
        r1     = rounds.get(1)
        final_round_num = max(rounds.keys())
        r_final = rounds.get(final_round_num)

        entry = {
            \'year\':        yr,
            \'rounds\':      rounds,
            \'round_count\': len(rounds),   # NEW: how many rounds exist this year
        }

        if r1 is not None:
            r1_trend.append({\'year\': yr, \'r1_cutoff\': r1})

        if r1 is not None and r_final is not None:
            drop = round(r_final - r1, 2)
            entry[\'r1_to_final_drop\'] = drop
            r1_to_final_drops.append(drop)

        result.append(entry)

    avg_drop = (round(sum(r1_to_final_drops) / len(r1_to_final_drops), 2)
                if r1_to_final_drops else 0)
    abs_avg  = abs(avg_drop)

    # Advice: ignore unreasonably large drops (data anomaly)
    if abs_avg > 15.0:
        advice = \'Cutoff data shows high variance — treat Round 1 as your benchmark.\'
    elif avg_drop < -1.0:
        advice = (f\'Cutoff drops avg {abs_avg:.1f} pts from Round 1 to final — \'
                  f\'if you miss Round 1, applying in Round 2 is still viable.\')
    elif avg_drop > 1.0:
        advice = (f\'Cutoff rises avg {abs_avg:.1f} pts from Round 1 to final — \'
                  f\'Round 1 is your best chance to secure admission.\')
    else:
        advice = \'Cutoff is stable across rounds — any round gives a fair chance.\'

    return jsonify({
        \'status\':               \'success\',
        \'college\':              college,
        \'branch\':               branch,
        \'seat_type\':            seat_type,
        \'years\':                result,
        \'r1_trend\':             r1_trend,      # NEW: clean R1 year-over-year array
        \'avg_r1_to_final_drop\': avg_drop,
        \'advice\':               advice,
    })

'''

# ── Apply patch ────────────────────────────────────────────────────────────────
if not os.path.exists(APP_PATH):
    print(f"❌  {APP_PATH} not found. Run from your backend folder.")
    exit(1)

with open(APP_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the block to replace: from CutoffPrediction class to end of round_trend function
pattern = r'# ── Cutoff Predictions Model.*?(?=\nif __name__)'
match = re.search(pattern, content, re.DOTALL)

if not match:
    print("❌  Could not find the prediction block in app.py.")
    print("    Make sure app.py contains '# ── Cutoff Predictions Model'")
    exit(1)

new_content = content[:match.start()] + REPLACEMENT.lstrip('\n') + content[match.end():]

# Write backup
backup_path = APP_PATH + '.bak'
with open(backup_path, 'w', encoding='utf-8') as f:
    f.write(content)
print(f"✅  Backup saved → {backup_path}")

with open(APP_PATH, 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f"✅  app.py patched successfully!")
print("""
Changes made to app.py:
  /api/predict/2026   — trend now uses R1 cutoff per year (not final-round)
  /api/predict/2026   — trend_direction computed from R1 values only
  /api/predict/2026   — confidence: High=8+, Medium=4+, Low<4 pts
  /api/trend/rounds   — added round_count per year to response
  /api/trend/rounds   — added r1_trend array (R1 cutoff per year)

Next steps:
  1. Run:  python predict_2026.py   (rebuilds predictions with all rounds)
  2. Run:  python patch_app_routes.py  (this file — patches app.py)
  3. Run:  python app.py            (restart Flask)
  4. Test: http://localhost:5000/api/predict/2026?college=COEP&branch=Computer+Engineering&seat_type=GOPENS
""")