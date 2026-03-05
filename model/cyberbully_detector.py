import re
import joblib
import os
import pandas as pd
import numpy as np
from nltk.stem import WordNetLemmatizer
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GridSearchCV, cross_val_score
from scipy.sparse import hstack
from textblob import TextBlob
from sklearn.metrics import classification_report
import logging
import nltk

# Ensure NLTK resources are downloaded
nltk.download('wordnet')
nltk.download('stopwords')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CyberbullyDetector:
    def __init__(self, model_path="models/cyberbully_model.pkl", vectorizer_path="models/vectorizer.pkl"):
        self.model_path = model_path
        self.vectorizer_path = vectorizer_path
        self.classifier = None
        self.vectorizer = TfidfVectorizer(
            stop_words=None,
            max_features=7000,
            ngram_range=(1, 3)
        )
        self.is_trained = False
        self.high_risk_keywords = [
            r"\bkill\s+(yourself|urself|u)\b", r"\bgo\s+die\b", r"\bsuicide\b",
            r"\bend\s+your\s+life\b", r"\bbetter\s+off\s+dead\b",
            r"\bnobody\s+(likes|cares)\b", r"\bworthless\b",
            r"\byou\s+are\s+(stupid|idiot|loser)\b"
        ]
        self.medium_risk_keywords = [
            r"\bstupid\b", r"\bidiot\b", r"\bloser\b", r"\bshut\s+up\b",
            r"\bdumb\b", r"\bpathetic\b", r"\btrash\b", r"\bgarbage\b"
        ]
        self.load_model()

    def preprocess_text(self, text):
        text = text.lower().strip()
        # Keep punctuation conveying tone
        text = re.sub(r"[^\w\s!?]", "", text)
        lemmatizer = WordNetLemmatizer()
        words = text.split()
        words = [lemmatizer.lemmatize(word) for word in words]
        cleaned_text = ' '.join(words)
        sentiment = TextBlob(text).sentiment.polarity
        return cleaned_text, sentiment

    def train(self, texts, labels):
        processed_texts = [self.preprocess_text(text) for text in texts]
        X_text = self.vectorizer.fit_transform([text[0] for text in processed_texts])
        X_sentiment = np.array([text[1] for text in processed_texts]).reshape(-1, 1)
        X = hstack([X_text, X_sentiment])
        
        # Use balanced RandomForest with GridSearch
        param_grid = {
            'n_estimators': [100, 200],
            'max_depth': [None, 15, 25]
        }
        grid_search = GridSearchCV(
            RandomForestClassifier(class_weight='balanced_subsample', random_state=42),
            param_grid,
            cv=5,
            scoring='f1_weighted'
        )
        grid_search.fit(X, labels)
        self.classifier = grid_search.best_estimator_
        self.is_trained = True
        logger.info(f"Best RF params: {grid_search.best_params_}")
        self.save_model()

        # Cross-validation score
        scores = cross_val_score(self.classifier, X, labels, cv=5, scoring='f1_weighted')
        logger.info(f"CV F1 Score: {np.mean(scores):.3f}")

    def load_model(self):
        if os.path.exists(self.model_path) and os.path.exists(self.vectorizer_path):
            self.classifier = joblib.load(self.model_path)
            self.vectorizer = joblib.load(self.vectorizer_path)
            self.is_trained = True
            logger.info("Model and vectorizer loaded successfully.")
        else:
            logger.warning("Model files not found. Please train the model first.")

    def train_from_csv(self, csv_path, text_col="text", label_col="label"):
        df = pd.read_csv(csv_path)
        texts = df[text_col].astype(str).tolist()
        labels = df[label_col].tolist()
        self.train(texts, labels)

    def save_model(self, model_path=None, vectorizer_path=None):
        if model_path is None:
            model_path = self.model_path
        if vectorizer_path is None:
            vectorizer_path = self.vectorizer_path
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        os.makedirs(os.path.dirname(vectorizer_path), exist_ok=True)
        joblib.dump(self.classifier, model_path)
        joblib.dump(self.vectorizer, vectorizer_path)

    def _ml_prediction(self, text):
        if not (self.vectorizer and self.classifier):
            return 0, [0.0, 0.0]
        text_vec = self.vectorizer.transform([text[0]])
        sentiment = np.array([text[1]]).reshape(1, -1)
        X = hstack([text_vec, sentiment]).toarray()
        prediction = self.classifier.predict(X)[0]
        proba = self.classifier.predict_proba(X)[0]
        return prediction, proba

    def _rule_based_adjustment(self, text, prediction, proba):
        risk_score = proba[1] * 10
        confidence = max(proba)
        risk_level = 'low'

        # Weighted keyword boost
        num_high_hits = sum(bool(re.search(kw, text[0], re.IGNORECASE)) for kw in self.high_risk_keywords)
        num_medium_hits = sum(bool(re.search(kw, text[0], re.IGNORECASE)) for kw in self.medium_risk_keywords)
        keyword_boost = 2 * num_high_hits + 1 * num_medium_hits
        risk_score = min(risk_score + keyword_boost, 10)

        if risk_score >= 7:
            risk_level = "high"
        elif risk_score >= 4:
            risk_level = "medium"
        else:
            risk_level = "low"

        return risk_level, risk_score, confidence

    def predict(self, text):
        try:
            cleaned_text = self.preprocess_text(text)
            if not cleaned_text[0].strip():
                return {
                    'risk_score': 0.0, 'risk_level': 'low', 'confidence': 0.0,
                    'details': 'Empty or invalid text'
                }
            prediction, proba = self._ml_prediction(cleaned_text)
            risk_level, risk_score, confidence = self._rule_based_adjustment(cleaned_text, prediction, proba)

            # Dynamic confidence adjustment
            if risk_level == "high" and confidence < 0.6:
                risk_level = "medium"
            elif risk_level == "medium" and confidence < 0.5:
                risk_level = "low"

            classification = "Cyberbullying" if risk_level in ["high", "medium"] else "Not Cyberbullying"
            return {
                'risk_score': round(risk_score, 1),
                'risk_level': risk_level,
                'confidence': round(confidence, 2),
                'details': f'Classified as: {classification}'
            }
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return {
                'risk_score': 0.0,
                'risk_level': 'low',
                'confidence': 0.0,
                'details': f'Error in prediction: {str(e)}'
            }

    def analyze_tweets(self, tweets):
        results = []
        high, medium, low = 0, 0, 0
        for tweet in tweets:
            res = self.predict(tweet)
            results.append({
                "tweet": tweet,
                "risk_level": res["risk_level"],
                "risk_score": res["risk_score"],
                "confidence": res["confidence"],
                "details": res["details"]
            })
            if res["risk_level"] == "high":
                high += 1
            elif res["risk_level"] == "medium":
                medium += 1
            else:
                low += 1
        total = len(tweets)
        summary = {
            "total_tweets": total,
            "high": high,
            "medium": medium,
            "low": low,
            "cyberbully_percentage": round((high + medium) / total * 100, 2) if total > 0 else 0,
            "non_cyberbully_percentage": round(low / total * 100, 2) if total > 0 else 0,
            "results": results
        }
        return summary

    def evaluate(self, texts, labels):
        processed_texts = [self.preprocess_text(text) for text in texts]
        X_text = self.vectorizer.transform([text[0] for text in processed_texts])
        X_sentiment = np.array([text[1] for text in processed_texts]).reshape(-1, 1)
        X = hstack([X_text, X_sentiment]).toarray()
        predictions = self.classifier.predict(X)
        print(classification_report(labels, predictions))