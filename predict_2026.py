"""
predict_2026.py  — FINAL FIXED VERSION
=======================================
What changed from the previous version:

  FIX 1 ► REMOVED FEATURE LEAK  (critical)
    r1_to_final_drop was included in feature_cols but for 2026 we do not
    have real R1 data yet — we were approximating it with a historical avg.
    Using this approximated value as a training feature causes leakage:
    the model over-weights a signal that will not be available at inference time.
    Removed 'r1_to_final_drop' from feature_cols.
    The feature is still computed internally for reference / inspection only.

  FIX 2 ► REMOVED r1_cutoff FROM feature_cols  (partial leak)
    For 2026 we project r1_cutoff using trend_slope — this projected value
    differs in distribution from the real R1 cutoffs in training rows.
    Keeping it in features confuses the model.  Dropped from feature_cols.
    trend_slope alone captures the same directional signal cleanly.

  FIX 3 ► MAE GUARD
    Prints a clear warning if validation MAE > 3.0 so you know the model
    needs more data (usually means 2022 data is missing).

  FIX 4 ► Safer label-encoder transform for 2026 rows
    Uses .map() with per-encoder closure instead of .transform() on the full
    column — avoids ValueError when a 2026 college name was never seen in training.

  Everything else is the same solid logic as the previous version:
    - All rounds x all years loaded (not just final-round)
    - Aggregate features from full dataset (mean, std, min, max, slope)
    - LightGBM with early stopping
    - Train: 2022-2024, Validate: 2025
    - Predict: 2026, clamped to +-12 pts of 2025 actual
    - Confidence: High=8+, Medium=4+, Low<4 data points

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


def confidence_label(data_points):
    """
    High   = 8+ data points  (3 rounds x 3 years = 9 -> solid)
    Medium = 4+ data points  (2 rounds x 2 years = 4 -> acceptable)
    Low    = <4 data points  (thin history -> treat with caution)
    """
    if data_points is None:
        return 'Low'
    if data_points >= 8:
        return 'High'
    if data_points >= 4:
        return 'Medium'
    return 'Low'


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    print("Connected to PostgreSQL")

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
    print("cutoff_predictions table ready")

    # ── Load ALL rows: all rounds, all years ───────────────────────────────────
    print("\nLoading ALL rounds from ALL years (2022-2025)...")
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
          f"({all_df['year'].nunique()} years x all rounds)")

    # Verify all 4 years are present — warn clearly if not
    for yr in [2022, 2023, 2024, 2025]:
        cnt = len(all_df[all_df['year'] == yr])
        flag = "OK" if cnt > 500 else "WARNING: LOW ROW COUNT - re-run import script!"
        print(f"  Year {yr}: {cnt:,} rows  [{flag}]")

    # ── Step 1: Aggregate features from the FULL dataset ──────────────────────
    print("\nBuilding aggregate features from full dataset...")

    # Total data points per key (all rounds x all years — up to ~12)
    dp = (all_df.groupby(KEY)['closing_percentile']
                .count()
                .reset_index()
                .rename(columns={'closing_percentile': 'total_data_points'}))

    # Statistical aggregates
    agg = (all_df.groupby(KEY)['closing_percentile']
                 .agg(['mean','std','min','max'])
                 .reset_index()
                 .rename(columns={'mean':'all_mean','std':'all_std',
                                   'min':'all_min','max':'all_max'}))
    agg['all_std'] = agg['all_std'].fillna(0)

    # Year-over-year trend slope (yearly avg across all rounds)
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

    # Round-1 data — used ONLY for internal r1_to_final_drop reference
    # NOT passed to the model (see FIX 1 and FIX 2)
    r1_data = (all_df[all_df['round'] == 1]
               [KEY + ['year', 'closing_percentile']]
               .rename(columns={'closing_percentile': 'r1_cutoff'}))

    print(f"  Aggregate features built for {len(agg):,} college+branch+seat combinations")

    # ── Step 2: Collapse to final round per year for training target ───────────
    print("\nCollapsing to final round per year (training target)...")
    max_round = (all_df.groupby(KEY + ['year'])['round']
                       .max()
                       .reset_index()
                       .rename(columns={'round': 'max_round'}))
    final = all_df.merge(max_round, on=KEY + ['year'])
    final = final[final['round'] == final['max_round']].drop(columns=['max_round'])
    final = final.reset_index(drop=True).copy()
    print(f"  Final-round rows: {len(final):,}")

    # Merge aggregate features
    final = (final
             .merge(agg, on=KEY, how='left')
             .merge(dp, on=KEY, how='left')
             .merge(trend_slope, on=KEY, how='left')
             .merge(r1_data, on=KEY + ['year'], how='left'))

    # r1_to_final_drop — internal reference only, NOT in feature_cols (FIX 1)
    final['r1_to_final_drop'] = (
        final['closing_percentile'] - final['r1_cutoff'].fillna(final['closing_percentile'])
    )

    # ── Step 3: Lag features ───────────────────────────────────────────────────
    final = final.sort_values(KEY + ['year']).reset_index(drop=True)
    final['prev_year_cutoff'] = (
        final.groupby(KEY)['closing_percentile']
             .shift(1)
             .fillna(final['all_mean'])
    )

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
    # FIX 1 + FIX 2: 'r1_to_final_drop' and 'r1_cutoff' removed from feature_cols.
    # Both would be approximated/leaked for 2026 inference. trend_slope covers the signal.
    feature_cols = [
        'year', 'years_available',
        'city_enc', 'college_type_enc', 'college_name_enc', 'branch_enc', 'seat_type_enc',
        'all_mean', 'all_std', 'all_min', 'all_max',
        'trend_slope', 'prev_year_cutoff',
        'total_data_points',
    ]

    train = final[final['year'] <= 2024].copy()
    val   = final[final['year'] == 2025].copy()

    X_train = train[feature_cols].fillna(0)
    y_train = train['closing_percentile']
    X_val   = val[feature_cols].fillna(0)
    y_val   = val['closing_percentile']

    print(f"\nTraining set : {len(train):,} rows (years 2022-2024)")
    print(f"Validation   : {len(val):,} rows (year 2025)")

    if len(val) == 0:
        print("\nWARNING: No 2025 validation rows! Run import_2025_fixed.py first.")
        print("Continuing without validation — predictions may be less accurate.")

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

    if len(val) > 0:
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[
                lgb.early_stopping(50, verbose=False),
                lgb.log_evaluation(period=-1),
            ],
        )
    else:
        model.fit(X_train, y_train)

    # ── Step 6: Accuracy report ────────────────────────────────────────────────
    if len(val) > 0:
        val_preds = model.predict(X_val)
        mae       = mean_absolute_error(y_val, val_preds)
        within_1  = np.mean(np.abs(val_preds - y_val) <= 1.0) * 100
        within_2  = np.mean(np.abs(val_preds - y_val) <= 2.0) * 100
        within_5  = np.mean(np.abs(val_preds - y_val) <= 5.0) * 100

        print(f"\n{'='*55}")
        print("MODEL ACCURACY  (predict 2025 using 2022-2024 data)")
        print(f"  Algorithm           : LightGBM (best_iter={model.best_iteration_})")
        print(f"  Mean Absolute Error : {mae:.2f} percentile points")
        print(f"  Within +-1 point    : {within_1:.1f}%")
        print(f"  Within +-2 points   : {within_2:.1f}%")
        print(f"  Within +-5 points   : {within_5:.1f}%")
        print(f"{'='*55}")

        # FIX 3: MAE guard — warn loudly if accuracy is poor
        if mae > 3.0:
            print(f"\n  WARNING: MAE={mae:.2f} is HIGH (target < 3.0).")
            print(f"  This usually means 2022 data is missing or incomplete.")
            print(f"  Run: python verify_data.py  to check your data.")
            print(f"  Then re-run: python import_all_years.py")
        elif mae > 2.0:
            print(f"\n  INFO: MAE={mae:.2f} is acceptable. More historical data would improve it.")
        else:
            print(f"\n  MAE={mae:.2f} — good accuracy!")

        top7 = sorted(zip(feature_cols, model.feature_importances_),
                      key=lambda x: x[1], reverse=True)[:7]
        print("\nTop 7 feature importances:")
        for feat, imp in top7:
            print(f"  {feat:<30} {imp:.0f}")

    # ── Step 7: Predict 2026 ──────────────────────────────────────────────────
    print("\nPredicting 2026 cutoffs...")

    pred_df = final[final['year'] == 2025].copy()

    if len(pred_df) == 0:
        fallback_year = final['year'].max()
        pred_df = final[final['year'] == fallback_year].copy()
        print(f"\n  WARNING: No 2025 data found in cutoffs table!")
        print(f"  Falling back to year {fallback_year} as the prediction base.")
        print(f"  FIX: Run python import_2025_fixed.py FIRST, then re-run predict_2026.py")
    else:
        print(f"  Using 2025 final-round data as base ({len(pred_df):,} rows)")

    pred_df = pred_df.copy()
    pred_df['year']             = 2026
    pred_df['prev_year_cutoff'] = pred_df['closing_percentile']  # 2025 actual -> lag

    # FIX 4: Safer label-encoder transform — handles colleges not seen in training
    for col, enc in le.items():
        known = set(enc.classes_)
        pred_df[f'{col}_enc'] = pred_df[col].fillna('Unknown').apply(
            lambda v: enc.transform([v])[0] if v in known else -1
        )

    X_pred    = pred_df[feature_cols].fillna(0)
    raw_preds = model.predict(X_pred)
    base_2025 = pred_df['closing_percentile'].values

    # Safety clamp: +-12 pts of 2025 actual. Real CAP cutoffs rarely shift more.
    clamped = np.clip(raw_preds, base_2025 - 12.0, base_2025 + 12.0)
    clamped = np.clip(clamped, 0, 100)
    pred_df['predicted_percentile'] = clamped

    # Confidence based on total_data_points (all rounds x all years, up to ~12)
    pred_df['confidence'] = pred_df['total_data_points'].apply(confidence_label)

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
    print(f"DONE!  {total_saved:,} predictions saved for 2026")
    print("\nConfidence breakdown:")
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
    print("All done!  Restart your Flask server to use new predictions.")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()