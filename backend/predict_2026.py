"""
predict_2026.py  — FIXED VERSION
=================================
Trains a GradientBoostingRegressor on 2022-2025 cutoff data
and predicts 2026 closing cutoffs for every college+branch+seat_type.

Run:
    python predict_2026.py
"""

import psycopg2
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_absolute_error
import warnings
warnings.filterwarnings('ignore')

DB_CONFIG = {
    'host':     'localhost',
    'port':     5432,
    'dbname':   'explainai_db',
    'user':     'postgres',
    'password': 'postgres',
}

def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    print("✅ Connected to PostgreSQL")

    # Create predictions table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cutoff_predictions (
            id                   SERIAL PRIMARY KEY,
            college_name         VARCHAR(200),
            college_code         VARCHAR(20),
            branch               VARCHAR(300),
            seat_type            VARCHAR(30),
            city                 VARCHAR(100),
            college_type         VARCHAR(100),
            predicted_year       INTEGER,
            predicted_percentile FLOAT,
            confidence           VARCHAR(10),
            data_points          INTEGER,
            created_at           TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("DELETE FROM cutoff_predictions WHERE predicted_year = 2026;")
    conn.commit()
    print("✅ cutoff_predictions table ready")

    # Load all data
    print("\nLoading data from database...")
    cur.execute("""
        SELECT college_name, college_code, branch, seat_type,
               closing_percentile, year, round, city, college_type
        FROM cutoffs
        WHERE year BETWEEN 2022 AND 2025
          AND closing_percentile > 0
        ORDER BY college_name, branch, seat_type, year, round
    """)
    rows = cur.fetchall()
    cols = ['college_name','college_code','branch','seat_type',
            'closing_percentile','year','round','city','college_type']
    df = pd.DataFrame(rows, columns=cols)
    print(f"Loaded {len(df):,} rows covering {df['year'].nunique()} years")

    # Get the final (highest) round per college+branch+seat+year using merge
    max_rounds = (df.groupby(['college_name','branch','seat_type','year'])['round']
                    .max()
                    .reset_index()
                    .rename(columns={'round': 'max_round'}))
    final = df.merge(max_rounds, on=['college_name','branch','seat_type','year'])
    final = final[final['round'] == final['max_round']].drop(columns=['max_round'])
    final = final.reset_index(drop=True).copy()
    print(f"Final round rows: {len(final):,}")

    # Feature engineering
    print("\nEngineering features...")

    le_city    = LabelEncoder()
    le_type    = LabelEncoder()
    le_college = LabelEncoder()
    le_branch  = LabelEncoder()
    le_seat    = LabelEncoder()

    final['city_enc']    = le_city.fit_transform(final['city'].fillna('Maharashtra'))
    final['type_enc']    = le_type.fit_transform(final['college_type'].fillna('Other'))
    final['college_enc'] = le_college.fit_transform(final['college_name'])
    final['branch_enc']  = le_branch.fit_transform(final['branch'])
    final['seat_enc']    = le_seat.fit_transform(final['seat_type'])

    key_cols = ['college_name', 'branch', 'seat_type']

    grp = final.groupby(key_cols)['closing_percentile']
    final['mean_cutoff'] = grp.transform('mean')
    final['std_cutoff']  = grp.transform('std').fillna(0)
    final['min_cutoff']  = grp.transform('min')
    final['max_cutoff']  = grp.transform('max')
    final['data_points'] = grp.transform('count')

    # Trend (slope) — using transform with a safe lambda
    def safe_slope(g):
        years = final.loc[g.index, 'year'].values
        vals  = g.values
        if len(set(years)) < 2:
            return pd.Series(0.0, index=g.index)
        slope = np.polyfit(years, vals, 1)[0]
        return pd.Series(slope, index=g.index)

    final['trend'] = grp.transform(lambda g: safe_slope(g))

    # Lag feature: previous year cutoff
    final = final.sort_values(['college_name','branch','seat_type','year']).reset_index(drop=True)
    final['prev_year_cutoff'] = (final.groupby(key_cols)['closing_percentile']
                                       .shift(1)
                                       .fillna(final['mean_cutoff']))

    # Round 1 cutoff for each year
    r1 = (df[df['round'] == 1]
            [['college_name','branch','seat_type','year','closing_percentile']]
            .rename(columns={'closing_percentile': 'r1_cutoff'}))
    final = final.merge(r1, on=['college_name','branch','seat_type','year'], how='left')
    final['r1_to_final_drop'] = (final['closing_percentile']
                                  - final['r1_cutoff'].fillna(final['closing_percentile']))

    print(f"Feature engineering done. Rows: {len(final):,}")

    feature_cols = [
        'year', 'city_enc', 'type_enc', 'college_enc', 'branch_enc', 'seat_enc',
        'mean_cutoff', 'std_cutoff', 'min_cutoff', 'max_cutoff',
        'trend', 'prev_year_cutoff', 'r1_to_final_drop', 'data_points'
    ]

    # Train on 2022-2024, validate on 2025
    train = final[final['year'] <= 2024].copy()
    val   = final[final['year'] == 2025].copy()

    X_train = train[feature_cols].fillna(0)
    y_train = train['closing_percentile']
    X_val   = val[feature_cols].fillna(0)
    y_val   = val['closing_percentile']

    print("\nTraining GradientBoostingRegressor...")
    model = GradientBoostingRegressor(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=5,
        min_samples_leaf=5,
        subsample=0.8,
        random_state=42
    )
    model.fit(X_train, y_train)

    # Validation accuracy
    val_preds = model.predict(X_val)
    mae       = mean_absolute_error(y_val, val_preds)
    within_1  = np.mean(np.abs(val_preds - y_val) <= 1.0) * 100
    within_2  = np.mean(np.abs(val_preds - y_val) <= 2.0) * 100
    within_5  = np.mean(np.abs(val_preds - y_val) <= 5.0) * 100

    print(f"\n{'='*50}")
    print("MODEL ACCURACY (predicting 2025 from 2022-2024 data):")
    print(f"  Mean Absolute Error : {mae:.2f} percentile points")
    print(f"  Within ±1 point     : {within_1:.1f}%")
    print(f"  Within ±2 points    : {within_2:.1f}%")
    print(f"  Within ±5 points    : {within_5:.1f}%")
    print(f"{'='*50}")

    # Predict 2026 using 2025 as base
    print("\nPredicting 2026 cutoffs...")
    pred_df = final[final['year'] == 2025].copy()
    if len(pred_df) == 0:
        pred_df = final[final['year'] == 2024].copy()
        print("  (Using 2024 as base)")

    pred_df = pred_df.copy()
    pred_df['year']             = 2026
    pred_df['prev_year_cutoff'] = pred_df['closing_percentile']

    X_pred = pred_df[feature_cols].fillna(0)
    pred_df['predicted_percentile'] = np.clip(model.predict(X_pred), 0, 100)

    pred_df['confidence'] = pred_df['data_points'].apply(
        lambda n: 'High' if n >= 4 else ('Medium' if n >= 2 else 'Low')
    )

    print(f"Generated {len(pred_df):,} predictions")

    # Save to DB
    print("\nSaving to database...")
    saved = 0
    for _, row in pred_df.iterrows():
        cur.execute("""
            INSERT INTO cutoff_predictions
                (college_name, college_code, branch, seat_type,
                 city, college_type, predicted_year,
                 predicted_percentile, confidence, data_points)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            row['college_name'], row.get('college_code',''),
            row['branch'], row['seat_type'],
            row['city'], row['college_type'],
            2026,
            round(float(row['predicted_percentile']), 4),
            row['confidence'],
            int(row['data_points']),
        ))
        saved += 1
        if saved % 2000 == 0:
            conn.commit()
            print(f"  Saved {saved:,}...")

    conn.commit()

    # Final report
    cur.execute("SELECT COUNT(*) FROM cutoff_predictions WHERE predicted_year=2026")
    total_saved = cur.fetchone()[0]

    cur.execute("""
        SELECT confidence, COUNT(*)
        FROM cutoff_predictions WHERE predicted_year=2026
        GROUP BY confidence ORDER BY confidence
    """)
    print(f"\n{'='*50}")
    print(f"✅ DONE! {total_saved:,} predictions saved for 2026")
    print("\nConfidence breakdown:")
    for conf, cnt in cur.fetchall():
        print(f"  {conf:<8}: {cnt:,}")

    cur.execute("""
        SELECT college_name, branch, seat_type, predicted_percentile, confidence
        FROM cutoff_predictions
        WHERE predicted_year=2026 AND seat_type='GOPENS' AND confidence='High'
        ORDER BY predicted_percentile DESC LIMIT 8
    """)
    print("\nTop 8 predicted GOPENS cutoffs for 2026:")
    print(f"  {'College':<45} {'Branch':<30} Predicted")
    print("  " + "-"*85)
    for name, branch, seat, pct, conf in cur.fetchall():
        print(f"  {name[:44]:<45} {branch[:29]:<30} {pct:.2f}")

    cur.close()
    conn.close()
    print(f"\n{'='*50}")
    print("Next: python add_prediction_api.py")
    print("="*50)

if __name__ == '__main__':
    main()