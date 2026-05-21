import asyncio
import json
import time
import httpx
import numpy as np
import pandas as pd
import pandas_ta as ta
from collections import deque
import xgboost as xgb
from sklearn.metrics import classification_report, confusion_matrix
import matplotlib.pyplot as plt
import os
from predictor import FEATURE_COLUMNS
from price_feed import compute_features

BINANCE_REST_URL = "https://api.binance.com/api/v3/klines"

async def fetch_90_days_data():
    """Fetch last 90 days of BTC/USDT 5m OHLCV from Binance REST API."""
    # Calculate start time (90 days ago in milliseconds)
    end_time = int(time.time() * 1000)
    start_time = end_time - (90 * 24 * 60 * 60 * 1000)  # 90 days in ms

    all_candles = []
    # We'll fetch in chunks of 1000 candles, going backwards in time
    current_end_time = end_time

    async with httpx.AsyncClient() as client:
        while True:
            params = {
                "symbol": "BTCUSDT",
                "interval": "5m",
                "limit": 1000,
                "endTime": current_end_time
            }

            response = await client.get(BINANCE_REST_URL, params=params)
            response.raise_for_status()
            klines = response.json()

            if not klines:
                break

            # Convert klines to our candle format
            for kline in klines:
                candle = {
                    "timestamp": int(kline[0]),
                    "open": float(kline[1]),
                    "high": float(kline[2]),
                    "low": float(kline[3]),
                    "close": float(kline[4]),
                    "volume": float(kline[5]),
                    "closed": True  # Historical candles are considered closed
                }
                all_candles.append(candle)

            # Update end_time to the oldest candle's timestamp in this batch for next iteration
            oldest_timestamp = klines[0][0]
            if oldest_timestamp < start_time:
                # We've fetched enough data (older than 90 days)
                break

            current_end_time = oldest_timestamp - 1  # Avoid duplicate

            # Safety break if we get less than 1000 candles (means we've reached the limit)
            if len(klines) < 1000:
                break

    # Sort candles by timestamp ascending (oldest first)
    all_candles.sort(key=lambda x: x["timestamp"])

    print(f"Fetched {len(all_candles)} candles from {all_candles[0]['timestamp']} to {all_candles[-1]['timestamp']}")
    return all_candles

def prepare_features_and_labels(candles):
    """Compute features and labels for training."""
    features_list = []
    labels = []

    # We need at least 200 candles to compute features for the first candle
    for i in range(199, len(candles) - 1):  # -1 because we need next candle for label
        # Get window of 200 candles ending at current index i
        window = candles[i-199:i+1]
        candle_deque = deque(window, maxlen=200)

        # Compute features
        features = compute_features(candle_deque)

        # Label: 1 if next close > current close else 0
        current_close = candles[i]["close"]
        next_close = candles[i+1]["close"]
        label = 1 if next_close > current_close else 0

        features_list.append(features)
        labels.append(label)

    print(f"Prepared {len(features_list)} feature samples")
    return features_list, labels

def features_to_array(features_list):
    """Convert list of feature dicts to numpy array in FEATURE_COLUMNS order."""
    X = []
    for features in features_list:
        # Extract values in the order of FEATURE_COLUMNS
        values = [features[col] for col in FEATURE_COLUMNS]
        X.append(values)
    return np.array(X, dtype=np.float32)

def main():
    print("Fetching 90 days of BTC/USDT 5m data...")
    candles = asyncio.run(fetch_90_days_data())

    print("Computing features and labels...")
    features_list, labels = prepare_features_and_labels(candles)

    if len(features_list) == 0:
        print("Error: No features generated. Check data fetching.")
        return

    # Convert features to array
    X = features_to_array(features_list)
    y = np.array(labels)

    print(f"Feature matrix shape: {X.shape}")
    print(f"Labels shape: {y.shape}")

    # Split 80/20 by time (no shuffle)
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    print(f"Training set: {X_train.shape[0]} samples")
    print(f"Validation set: {X_val.shape[0]} samples")

    # Train XGBoost classifier
    print("Training XGBoost model...")
    params = {
        "n_estimators": 500,
        "max_depth": 6,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "seed": 42
    }

    # Create DMatrices
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)

    # Train with early stopping
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=500,
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=20,
        verbose_eval=False
    )

    # Predict on validation set
    y_pred_prob = model.predict(dval)
    y_pred = (y_pred_prob > 0.5).astype(int)

    # Print evaluation metrics
    print("\n=== Validation Results ===")
    print(classification_report(y_val, y_pred, target_names=["DOWN", "UP"]))
    print("Confusion Matrix:")
    print(confusion_matrix(y_val, y_pred))

    # Feature importance
    importance = model.get_score(importance_type="gain")
    # Sort by importance
    sorted_importance = sorted(importance.items(), key=lambda x: x[1], reverse=True)

    print("\n=== Feature Importance (Gain) ===")
    for feat, imp in sorted_importance:
        print(f"{feat}: {imp}")

    # Save model
    model_dir = "model"
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, "btc_5m_model.json")
    model.save_model(model_path)
    print(f"\nModel saved to {model_path}")

    # Save feature importance plot
    plt.figure(figsize=(10, 6))
    features = [item[0] for item in sorted_importance]
    gains = [item[1] for item in sorted_importance]
    plt.barh(range(len(features)), gains, align='center')
    plt.yticks(range(len(features)), features)
    plt.xlabel('Gain')
    plt.title('Feature Importance')
    plt.gca().invert_yaxis()  # Highest gain on top
    plt.tight_layout()
    plot_path = os.path.join(model_dir, "feature_importance.png")
    plt.savefig(plot_path)
    plt.close()
    print(f"Feature importance plot saved to {plot_path}")

    # Save training report
    report_path = os.path.join(model_dir, "training_report.txt")
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    accuracy = accuracy_score(y_val, y_val)
    precision = precision_score(y_val, y_val, zero_division=0)
    recall = recall_score(y_val, y_val, zero_division=0)
    f1 = f1_score(y_val, y_val, zero_division=0)

    with open(report_path, 'w') as f:
        f.write(f"Accuracy: {accuracy:.4f}\n")
        f.write(f"Precision: {precision:.4f}\n")
        f.write(f"Recall: {recall:.4f}\n")
        f.write(f"F1 Score: {f1:.4f}\n")
    print(f"Training report saved to {report_path}")

if __name__ == "__main__":
    main()