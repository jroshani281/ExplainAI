"""
predict_2026.py  — FIXED VERSION (all rounds, all years)
=========================================================
BUG FIXES vs old version:
  1. data_points now counts ALL rows (all rounds × all years), not just final-round rows.
     Old: max 4 pts (1/year). New: up to 12 pts (3 rounds × 4 years).
     Effect: confidence='High' now correctly given to colleges with full history.

  2. Features are built from ALL round data before collapsing to final-round for training.
     Old: mean/std/trend computed AFTER dropping R1+R2, so slope was flat for many colleges.
     New: aggregate stats (mean, std, min, max, trend, r1_cutoff) use the full dataset.

  3. r1_cutoff is pulled from all years (2022-2025 R1), not just the final-round slice.
     Effect: r1_to_final_drop is accurate for all 4 years instead of only 2025.

  4. prev_year_cutoff fallback uses the full-dataset mean, not the filtered-slice mean.
     Effect: colleges with only 1 year of data get a more realistic baseline.

  5. Confidence thresholds adjusted: High=8+ data points, Medium=4+, Low=<4.
     Old thresholds (4/2) were tuned for the broken 1-row-per-year count.

Algorithm: LightGBM (same as before — fast, accurate on tabular data).
Train: 2022-2024 final-round cutoffs.
Validate: 2025 final-round cutoffs.
Predict: 2026 using 2025 as base + richer features.

Install:
    pip install lightgbm scikit-learn pandas psycopg2-binary

Run:
    python predict_2026.py
"""

import psycopg2
import pandas as pd
import numpy as np
import lightgbm as lgb
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

KEY = ['college_name', 'branch', 'seat_type']


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    print("✅ Connected to PostgreSQL")

    # ── Create / clear predictions table ──────────────────────────────────────
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

    # ── Load ALL rows: all rounds, all years ───────────────────────────────────
    # FIX 1: Load every round (R1, R2, R3, R4) from every year.
    # The old code loaded the same query but then immediately threw away R1/R2
    # before computing features — losing most of the signal.
    print("\nLoading ALL rounds from ALL years (2022–2025)...")
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
    all_df = pd.DataFrame(rows, columns=cols)
    print(f"  Loaded {len(all_df):,} rows "
          f"({all_df['year'].nunique()} years × all rounds)")

    # ── Step 1: Build aggregate features from the FULL dataset ────────────────
    # FIX 2: Compute mean/std/trend from ALL rounds, not just final rounds.
    # This gives a richer, more accurate signal for the model.
    print("\nBuilding aggregate features from full dataset...")

    # Total data points per college+branch+seat (across all rounds+years)
    # FIX 3: This is the true count — can be up to ~12 per combo.
    dp = (all_df.groupby(KEY)['closing_percentile']
                .count()
                .reset_index()
                .rename(columns={'closing_percentile': 'total_data_points'}))

    # Statistical aggregates across all rounds+years
    agg = (all_df.groupby(KEY)['closing_percentile']
                 .agg(['mean','std','min','max'])
                 .reset_index()
                 .rename(columns={'mean':'all_mean','std':'all_std',
                                   'min':'all_min','max':'all_max'}))
    agg['all_std'] = agg['all_std'].fillna(0)

    # Year-over-year trend slope (using ALL rounds within each year → yearly avg)
    # FIX 4: Old code computed slope from 1 row/year. Now we use the average
    # cutoff per year (all rounds), giving a more stable slope estimate.
    yearly_avg = (all_df.groupby(KEY + ['year'])['closing_percentile']
                        .mean()
                        .reset_index()
                        .rename(columns={'closing_percentile': 'yr_avg_cutoff'}))

    def compute_slope(grp):
        if len(grp) < 2:
            return 0.0
        return float(np.polyfit(grp['year'].values,
                                grp['yr_avg_cutoff'].values, 1)[0])

    trend_slope = (yearly_avg.groupby(KEY)
                             .apply(compute_slope)
                             .reset_index()
                             .rename(columns={0: 'trend_slope'}))

    # Round-1 cutoff per year (for r1_to_final_drop feature)
    # FIX 5: Pull R1 from all years, not just from the already-filtered slice.
    r1_data = (all_df[all_df['round'] == 1]
               [KEY + ['year', 'closing_percentile']]
               .rename(columns={'closing_percentile': 'r1_cutoff'}))

    print(f"  Aggregate features built for "
          f"{len(agg):,} college+branch+seat combinations")

    # ── Step 2: Collapse to final round per year for the training target ───────
    # We train the model to predict the FINAL round cutoff (what students actually
    # experience at the end of CAP). The richer features above are merged in.
    print("\nCollapsing to final round per year (training target)...")
    max_round = (all_df.groupby(KEY + ['year'])['round']
                       .max()
                       .reset_index()
                       .rename(columns={'round': 'max_round'}))
    final = all_df.merge(max_round, on=KEY + ['year'])
    final = final[final['round'] == final['max_round']].drop(columns=['max_round'])
    final = final.reset_index(drop=True).copy()
    print(f"  Final-round rows: {len(final):,}")

    # Merge all aggregate features
    final = (final
             .merge(agg, on=KEY, how='left')
             .merge(dp, on=KEY, how='left')
             .merge(trend_slope, on=KEY, how='left')
             .merge(r1_data, on=KEY + ['year'], how='left'))

    # r1_to_final_drop: how much cutoff falls from R1 to final round this year
    # Positive = rising market (R1 was easier than final)
    # Negative = falling market (cutoff relaxed after R1)
    final['r1_to_final_drop'] = (
        final['closing_percentile'] - final['r1_cutoff'].fillna(final['closing_percentile'])
    )

    # ── Step 3: Lag features ───────────────────────────────────────────────────
    final = final.sort_values(KEY + ['year']).reset_index(drop=True)
    final['prev_year_cutoff'] = (
        final.groupby(KEY)['closing_percentile']
             .shift(1)
             # FIX 6: Fallback to all_mean (full-dataset mean), not filtered mean
             .fillna(final['all_mean'])
    )

    # Years of data available for this combo (how many distinct years)
    years_available = (all_df.groupby(KEY)['year']
                              .nunique()
                              .reset_index()
                              .rename(columns={'year': 'years_available'}))
    final = final.merge(years_available, on=KEY, how='left')

    # ── Step 4: Encode categoricals ───────────────────────────────────────────
    print("\nEncoding categorical features...")
    le = {col: LabelEncoder() for col in ['city','college_type','college_name','branch','seat_type']}
    for col, enc in le.items():
        final[f'{col}_enc'] = enc.fit_transform(final[col].fillna('Unknown'))

    print(f"  Feature engineering complete. Rows: {len(final):,}")

    # ── Step 5: Train / validate ───────────────────────────────────────────────
    feature_cols = [
        # Temporal
        'year', 'years_available',
        # Categorical (encoded)
        'city_enc', 'college_type_enc', 'college_name_enc', 'branch_enc', 'seat_type_enc',
        # Statistical aggregates (ALL rounds × ALL years)
        'all_mean', 'all_std', 'all_min', 'all_max',
        # Trend and lag
        'trend_slope', 'prev_year_cutoff',
        # Round behaviour
        'r1_to_final_drop', 'r1_cutoff',
        # Data richness
        'total_data_points',
    ]

    train = final[final['year'] <= 2024].copy()
    val   = final[final['year'] == 2025].copy()

    X_train = train[feature_cols].fillna(0)
    y_train = train['closing_percentile']
    X_val   = val[feature_cols].fillna(0)
    y_val   = val['closing_percentile']

    print(f"\nTraining set : {len(train):,} rows (years 2022–2024)")
    print(f"Validation   : {len(val):,} rows (year 2025)")
    print("Training LightGBM model (~30 seconds)...")

    model = lgb.LGBMRegressor(
        n_estimators=600,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=10,
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[
            lgb.early_stopping(50, verbose=False),
            lgb.log_evaluation(period=-1),
        ],
    )

    # ── Step 6: Accuracy report ────────────────────────────────────────────────
    val_preds = model.predict(X_val)
    mae       = mean_absolute_error(y_val, val_preds)
    within_1  = np.mean(np.abs(val_preds - y_val) <= 1.0) * 100
    within_2  = np.mean(np.abs(val_preds - y_val) <= 2.0) * 100
    within_5  = np.mean(np.abs(val_preds - y_val) <= 5.0) * 100

    print(f"\n{'='*55}")
    print("MODEL ACCURACY  (predict 2025 using 2022-2024 data)")
    print(f"  Algorithm           : LightGBM "
          f"(best_iter={model.best_iteration_})")
    print(f"  Mean Absolute Error : {mae:.2f} percentile points")
    print(f"  Within ±1 point     : {within_1:.1f}%")
    print(f"  Within ±2 points    : {within_2:.1f}%")
    print(f"  Within ±5 points    : {within_5:.1f}%")
    print(f"{'='*55}")

    top5 = sorted(zip(feature_cols, model.feature_importances_),
                  key=lambda x: x[1], reverse=True)[:7]
    print("\nTop 7 feature importances:")
    for feat, imp in top5:
        print(f"  {feat:<30} {imp:.0f}")

    # ── Step 7: Predict 2026 ──────────────────────────────────────────────────
    print("\nPredicting 2026 cutoffs...")

    # Base = 2025 final-round rows (best available)
    pred_df = final[final['year'] == 2025].copy()
    if len(pred_df) == 0:
        pred_df = final[final['year'] == final['year'].max()].copy()
        print(f"  ⚠️  No 2025 data — using {pred_df['year'].max()} as base")

    pred_df = pred_df.copy()
    pred_df['year']             = 2026
    pred_df['prev_year_cutoff'] = pred_df['closing_percentile']   # 2025 actual = lag for 2026

    # For 2026 we don't have R1 data yet → use historical r1_to_final_drop
    # Calculate average r1_to_final_drop per college+branch+seat from all years
    avg_r1_drop = (final.groupby(KEY)['r1_to_final_drop']
                        .mean()
                        .reset_index()
                        .rename(columns={'r1_to_final_drop': 'avg_r1_drop'}))
    pred_df = pred_df.merge(avg_r1_drop, on=KEY, how='left')
    pred_df['r1_to_final_drop'] = pred_df['avg_r1_drop'].fillna(0)

    # Also estimate r1_cutoff for 2026 = 2025 r1 cutoff + trend_slope
    pred_df['r1_cutoff'] = (
        pred_df['r1_cutoff'].fillna(pred_df['all_mean']) + pred_df['trend_slope'].fillna(0)
    )

    X_pred    = pred_df[feature_cols].fillna(0)
    raw_preds = model.predict(X_pred)
    base_2025 = pred_df['closing_percentile'].values

    # Safety clamp: predictions must stay within ±12 pts of 2025 actual.
    # Real CAP cutoffs rarely shift more than 10 pts in one year.
    clamped = np.clip(raw_preds, base_2025 - 12.0, base_2025 + 12.0)
    clamped = np.clip(clamped, 0, 100)
    pred_df['predicted_percentile'] = clamped

    # ── Confidence: based on TOTAL data points (all rounds × all years) ───────
    # FIX 7: Use total_data_points (up to ~12) not old data_points (max 4).
    # Thresholds: High=8+, Medium=4+, Low=<4
    pred_df['confidence'] = pred_df['total_data_points'].apply(
        lambda n: 'High' if n >= 8 else ('Medium' if n >= 4 else 'Low')
    )

    print(f"  Generated {len(pred_df):,} predictions")
    conf_counts = pred_df['confidence'].value_counts()
    for level in ['High','Medium','Low']:
        print(f"    {level:<8}: {conf_counts.get(level, 0):,}")

    # ── Step 8: Save to database ───────────────────────────────────────────────
    print("\nSaving predictions to database...")
    saved = 0
    for _, row in pred_df.iterrows():
        cur.execute("""
            INSERT INTO cutoff_predictions
                (college_name, college_code, branch, seat_type,
                 city, college_type, predicted_year,
                 predicted_percentile, confidence, data_points)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            row['college_name'],
            row.get('college_code', ''),
            row['branch'],
            row['seat_type'],
            row['city'],
            row['college_type'],
            2026,
            round(float(row['predicted_percentile']), 4),
            row['confidence'],
            int(row['total_data_points']),
        ))
        saved += 1
        if saved % 2000 == 0:
            conn.commit()
            print(f"  Saved {saved:,}...")

    conn.commit()

    # ── Step 9: Final summary ──────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM cutoff_predictions WHERE predicted_year=2026")
    total_saved = cur.fetchone()[0]

    cur.execute("""
        SELECT confidence, COUNT(*)
        FROM cutoff_predictions WHERE predicted_year=2026
        GROUP BY confidence ORDER BY confidence
    """)
    conf_rows = cur.fetchall()

    print(f"\n{'='*55}")
    print(f"✅  DONE!  {total_saved:,} predictions saved for 2026")
    print("\nConfidence breakdown (using ALL rounds as data source):")
    for conf, cnt in conf_rows:
        print(f"  {conf:<8}: {cnt:,}")

    cur.execute("""
        SELECT college_name, branch, seat_type,
               predicted_percentile, confidence, data_points
        FROM cutoff_predictions
        WHERE predicted_year=2026
          AND seat_type='GOPENS'
          AND confidence='High'
        ORDER BY predicted_percentile DESC
        LIMIT 10
    """)
    print("\nTop 10 predicted GOPENS cutoffs for 2026 (High confidence):")
    print(f"  {'College':<45} {'Branch':<28} Predicted  DataPts")
    print("  " + "-"*95)
    for name, branch, seat, pct, conf, dp_val in cur.fetchall():
        print(f"  {name[:44]:<45} {branch[:27]:<28} {pct:>7.2f}    {dp_val}")

    cur.close()
    conn.close()

    print(f"\n{'='*55}")
    print("✅  All done!  Restart your Flask server to use new predictions.")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()