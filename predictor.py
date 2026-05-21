import json
import logging
import os
from typing import Dict

import numpy as np
import xgboost as xgb

from config import CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)

# Feature columns must match the order used in train_model.py
FEATURE_COLUMNS = [
    "timestamp",
    "close",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_mid",
    "bb_lower",
    "ema9",
    "ema21",
    "volume_change_pct",
    "candle_body_pct",
    "upper_wick_pct",
    "lower_wick_pct",
]


class Predictor:
    def __init__(self, model_path: str = "model/btc_5m_model.json"):
        """
        Initialize the predictor by loading the XGBoost model.
        If the model file is not found, fall back to rule-based logic.
        """
        self.use_fallback = False
        self.model = None

        if os.path.exists(model_path):
            try:
                self.model = xgb.Booster()
                self.model.load_model(model_path)
                logger.info(f"Loaded XGBoost model from {model_path}")
            except Exception as e:
                logger.warning(f"Failed to load XGBoost model: {e}. Falling back to rule-based logic.")
                self.use_fallback = True
        else:
            logger.warning(f"Model file not found at {model_path}. Falling back to rule-based logic.")
            self.use_fallback = True

    def get_feature_array(self, features: Dict) -> np.ndarray:
        """
        Convert features dictionary to a numpy array in the correct column order.
        Returns a 2D array with shape (1, n_features) for model prediction.
        """
        # Extract values in the order of FEATURE_COLUMNS
        values = [features[col] for col in FEATURE_COLUMNS]
        return np.array(values, dtype=np.float32).reshape(1, -1)

    def predict(self, features: Dict) -> Dict:
        """
        Predict the direction (UP/DOWN/HOLD) and confidence based on features.
        Returns a Signal dict.
        """
        if self.use_fallback:
            return self._predict_fallback(features)

        # Build feature array
        try:
            feature_array = self.get_feature_array(features)
        except KeyError as e:
            logger.error(f"Missing feature key: {e}. Falling back to rule-based logic.")
            return self._predict_fallback(features)
        except Exception as e:
            logger.error(f"Error building feature array: {e}. Falling back to rule-based logic.")
            return self._predict_fallback(features)

        # Run prediction
        try:
            # Predict probabilities: returns array of shape (n_samples, n_classes)
            probs = self.model.predict_proba(feature_array)[0]
            # Assuming class 0 = DOWN, class 1 = UP (adjust if your model is different)
            prob_down, prob_up = probs[0], probs[1]
        except Exception as e:
            logger.error(f"Error during model prediction: {e}. Falling back to rule-based logic.")
            return self._predict_fallback(features)

        # Determine direction based on confidence threshold
        if prob_up > CONFIDENCE_THRESHOLD:
            direction = "UP"
            confidence = prob_up
        elif prob_down > CONFIDENCE_THRESHOLD:
            direction = "DOWN"
            confidence = prob_down
        else:
            direction = "HOLD"
            confidence = 0.50  # Neutral confidence for HOLD

        # Construct signal dict
        signal: Dict = {
            "direction": direction,
            "confidence": float(confidence),
            "timestamp": features["timestamp"],
            "features": features,
        }
        return signal

    def _predict_fallback(self, features: Dict) -> Dict:
        """
        Rule-based fallback logic:
        - RSI < 30 -> UP with 0.70 confidence
        - RSI > 70 -> DOWN with 0.70 confidence
        - Otherwise -> HOLD with 0.50 confidence
        """
        rsi = features.get("rsi", 50.0)  # Default to neutral RSI if missing

        if rsi < 30:
            direction = "UP"
            confidence = 0.70
        elif rsi > 70:
            direction = "DOWN"
            confidence = 0.70
        else:
            direction = "HOLD"
            confidence = 0.50

        signal: Dict = {
            "direction": direction,
            "confidence": confidence,
            "timestamp": features["timestamp"],
            "features": features,
        }
        return signal