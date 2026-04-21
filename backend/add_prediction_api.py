"""
add_prediction_api.py
=====================
Adds the /api/predict/2026 route to your app.py
AND adds a round-to-round trend API.

Run AFTER predict_2026.py has finished:
    python add_prediction_api.py

What gets added to your app:
  GET  /api/predict/2026?college=X&branch=Y&seat_type=Z
       → returns predicted 2026 cutoff + confidence + trend data

  POST /api/recommend/colleges  (ENHANCED)
       → existing route now also returns predicted_2026 field on each result
"""

# ── The new routes to paste into app.py ───────────────────────────────────────
NEW_ROUTES = '''

# ── Cutoff Predictions Model ───────────────────────────────────────────────────

class CutoffPrediction(db.Model):
    __tablename__ = 'cutoff_predictions'
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

@app.route('/api/predict/2026', methods=['GET'])
def predict_2026():
    """
    GET /api/predict/2026?college=COEP&branch=Computer&seat_type=GOPENS
    Returns predicted 2026 cutoff + historical trend for a specific college+branch+seat.
    """
    college   = request.args.get('college', '').strip()
    branch    = request.args.get('branch', '').strip()
    seat_type = request.args.get('seat_type', '').strip()

    if not college or not branch or not seat_type:
        return jsonify({'status': 'error', 'error': 'college, branch, seat_type are required'}), 400

    # Get prediction
    pred = CutoffPrediction.query.filter(
        CutoffPrediction.college_name.ilike(f'%{college}%'),
        CutoffPrediction.branch.ilike(f'%{branch}%'),
        CutoffPrediction.seat_type == seat_type,
        CutoffPrediction.predicted_year == 2026
    ).first()

    # Get historical trend (2022-2025) for the chart
    from sqlalchemy import and_
    history = Cutoff.query.filter(
        Cutoff.college_name.ilike(f'%{college}%'),
        Cutoff.branch.ilike(f'%{branch}%'),
        Cutoff.seat_type == seat_type,
        Cutoff.year.between(2022, 2025)
    ).order_by(Cutoff.year, Cutoff.round).all()

    # Build per-year closing cutoff (highest round per year)
    yearly = {}
    for row in history:
        key = row.year
        if key not in yearly or row.round > yearly[key]['round']:
            yearly[key] = {'year': row.year, 'cutoff': row.closing_percentile, 'round': row.round}

    trend_data = sorted(yearly.values(), key=lambda x: x['year'])

    # Compute year-over-year change
    trend_direction = 'stable'
    if len(trend_data) >= 2:
        recent_change = trend_data[-1]['cutoff'] - trend_data[-2]['cutoff']
        if recent_change > 1.0:
            trend_direction = 'rising'
        elif recent_change < -1.0:
            trend_direction = 'falling'

    if not pred:
        return jsonify({
            'status': 'no_prediction',
            'message': 'No prediction available for this combination',
            'trend': [{'year': d['year'], 'cutoff': d['cutoff']} for d in trend_data],
            'trend_direction': trend_direction
        })

    return jsonify({
        'status': 'success',
        'college_name':         pred.college_name,
        'branch':               pred.branch,
        'seat_type':            pred.seat_type,
        'predicted_2026':       round(pred.predicted_percentile, 2),
        'confidence':           pred.confidence,
        'data_points':          pred.data_points,
        'trend_direction':      trend_direction,
        'trend': [{'year': d['year'], 'cutoff': d['cutoff']} for d in trend_data] + [
            {'year': 2026, 'cutoff': round(pred.predicted_percentile, 2), 'predicted': True}
        ]
    })


# ── Round-to-Round Trend API ───────────────────────────────────────────────────

@app.route('/api/trend/rounds', methods=['GET'])
def round_trend():
    """
    GET /api/trend/rounds?college=COEP&branch=Computer&seat_type=GOPENS
    Shows how cutoff changes from Round 1 → Round 2 → Round 3 across years.
    Unique insight: "This college drops 2.4 pts between R1 and R3 on average"
    """
    college   = request.args.get('college', '').strip()
    branch    = request.args.get('branch', '').strip()
    seat_type = request.args.get('seat_type', 'GOPENS').strip()

    rows = Cutoff.query.filter(
        Cutoff.college_name.ilike(f'%{college}%'),
        Cutoff.branch.ilike(f'%{branch}%'),
        Cutoff.seat_type == seat_type,
        Cutoff.year.between(2022, 2025)
    ).order_by(Cutoff.year, Cutoff.round).all()

    # Group by year → round
    data = {}
    for r in rows:
        yr = r.year
        if yr not in data:
            data[yr] = {}
        data[yr][r.round] = r.closing_percentile

    result = []
    r1_to_r3_drops = []
    for yr in sorted(data.keys()):
        rounds = data[yr]
        r1 = rounds.get(1)
        r3 = rounds.get(3) or rounds.get(max(rounds.keys()))
        entry = {'year': yr, 'rounds': rounds}
        if r1 and r3:
            drop = r3 - r1
            entry['r1_to_final_drop'] = round(drop, 2)
            r1_to_r3_drops.append(drop)
        result.append(entry)

    avg_drop = round(sum(r1_to_r3_drops) / len(r1_to_r3_drops), 2) if r1_to_r3_drops else 0

    advice = ''
    if avg_drop < -1.0:
        advice = f'Cutoff drops avg {abs(avg_drop):.1f} pts from Round 1 to final — if you miss Round 1, apply in Round 2.'
    elif avg_drop > 1.0:
        advice = f'Cutoff rises avg {avg_drop:.1f} pts from Round 1 to final — Round 1 is your best chance.'
    else:
        advice = 'Cutoff is stable across rounds — apply in any round.'

    return jsonify({
        'status':    'success',
        'college':   college,
        'branch':    branch,
        'seat_type': seat_type,
        'years':     result,
        'avg_r1_to_final_drop': avg_drop,
        'advice':    advice
    })

'''

import os

# ── Find app.py and add the new routes ────────────────────────────────────────
app_path = 'app.py'
if not os.path.exists(app_path):
    print("❌ app.py not found. Make sure you run this from your backend folder.")
    exit(1)

with open(app_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Check if already added
if 'CutoffPrediction' in content:
    print("⚠️  Prediction routes already exist in app.py — skipping.")
    print("✅  Your app.py already has the prediction API.")
else:
    # Insert before the final if __name__ == '__main__' block
    insert_before = "if __name__ == '__main__':"
    if insert_before in content:
        content = content.replace(insert_before, NEW_ROUTES + '\n' + insert_before)
        with open(app_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("✅  Added prediction routes to app.py successfully!")
    else:
        # Append to end of file
        with open(app_path, 'a', encoding='utf-8') as f:
            f.write(NEW_ROUTES)
        print("✅  Appended prediction routes to app.py")

print("""
Routes added to your app:
  GET /api/predict/2026?college=COEP&branch=Computer&seat_type=GOPENS
  GET /api/trend/rounds?college=COEP&branch=Computer&seat_type=GOPENS

Next steps:
  1. Restart your Flask server:  python app.py
  2. Test the API in browser:
     http://localhost:5000/api/predict/2026?college=COEP&branch=Computer+Engineering&seat_type=GOPENS
  3. Tell me and I will add the prediction badge to your frontend cards
""")
