"""Semantic intent classification and sentiment.

Intents are classified by embedding similarity (all-MiniLM) between the utterance and
labeled example utterances, so paraphrases ("where's my package?" ~ order status) work
without keyword lists. Used for analytics/UI by every agent mode and for routing in
offline mode; the LLM does its own understanding when available.
"""
import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.core.logging import get_logger
from app.services.rag_service import get_rag_service
from app.services.text_utils import normalize_spoken_ids

logger = get_logger("nlu")

INTENT_EXAMPLES: Dict[str, list] = {
    "greeting": ["hello", "hi there", "good morning", "hey, are you there?"],
    "goodbye": ["thank you, that's all", "thanks, bye", "that's everything, goodbye", "no, nothing else",
                "great, thanks for your help"],
    "order_status": ["what is the status of my order", "where is my order", "has my order shipped yet",
                     "can you track my package", "check on my order please", "what's happening with my order",
                     "when will my order arrive"],
    "delivery_issue": ["my package hasn't arrived", "my order is late", "the delivery is delayed",
                       "my package was marked delivered but I didn't get it", "my order never showed up",
                       "I'm still waiting for my delivery"],
    "order_cancellation": ["I want to cancel my order", "please cancel the order", "can I cancel it",
                           "I don't want this order anymore", "stop my order from shipping",
                           "I changed my mind about my purchase, cancel it"],
    "return_request": ["I want to return an item", "how do I send this back", "I'd like to return my headphones",
                       "start a return for my order", "the product doesn't fit, I want to return it"],
    "refund_request": ["I want a refund", "when will I get my money back", "where is my refund",
                       "can I get a refund for this", "how long do refunds take"],
    "product_information": ["tell me about the headphones", "does the watch have GPS", "what is the battery life",
                            "is this product waterproof", "what colors does it come in",
                            "what products do you sell"],
    "payment_issue": ["I was charged twice", "my payment failed", "there's a wrong charge on my card",
                      "what payment methods do you accept", "my card was declined", "billing problem"],
    "account_issue": ["I can't log into my account", "how do I reset my password", "update my phone number",
                      "change the address on my account", "my account is locked", "delete my account"],
    "policy_question": ["what is your return policy", "how long does shipping take", "do you ship internationally",
                        "what does the warranty cover", "what is your cancellation policy",
                        "how much is express shipping", "is there a restocking fee"],
    "complaint": ["this is terrible service", "I'm very unhappy with my purchase", "I want to file a complaint",
                  "the product arrived damaged", "your service is awful", "this is the worst experience"],
    "escalation": ["I want to talk to a human", "let me speak to a real person", "transfer me to an agent",
                   "can I talk to a manager", "connect me with customer service representative"],
}

KNOWLEDGE_INTENTS = {"policy_question", "product_information", "refund_request", "payment_issue", "account_issue"}
MIN_CONFIDENCE = 0.42
SECONDARY_CONFIDENCE = 0.55

_NEGATIVE = re.compile(
    r"\b(terrible|awful|horrible|worst|useless|ridiculous|unacceptable|angry|furious|frustrated|frustrating|"
    r"disappointed|disgusting|pathetic|hate|scam|fed up|sick of|waste of time|not helping|incompetent)\b", re.I)
_POSITIVE = re.compile(r"\b(thank|thanks|great|perfect|awesome|excellent|helpful|appreciate|wonderful|amazing)\b",
                       re.I)


class IntentClassifier:
    def __init__(self):
        self._labels = None
        self._matrix = None

    def _ensure(self):
        if self._matrix is not None:
            return
        embedder = get_rag_service().embedder
        labels, texts = [], []
        for intent, examples in INTENT_EXAMPLES.items():
            labels += [intent] * len(examples)
            texts += examples
        self._matrix = embedder.embed(texts)
        self._labels = labels

    def _best(self, vec) -> Tuple[str, float]:
        scores = self._matrix @ vec
        best: Dict[str, float] = {}
        for label, score in zip(self._labels, scores):
            best[label] = max(best.get(label, -1.0), float(score))
        return max(best.items(), key=lambda kv: kv[1])

    def classify_detailed(self, text: str) -> Tuple[str, float, List[str]]:
        """Classify the whole utterance and each clause, so multi-intent turns
        ("I was charged twice and I want a person") report every confident intent."""
        if not text.strip():
            return "general_inquiry", 0.0, []
        text = mask_identifiers(text)
        clauses = [c.strip() for c in re.split(r"[.!?;,]|\b(?:and|but|also|so)\b", text) if len(c.split()) >= 3]
        segments = [text] + (clauses if len(clauses) > 1 else [])
        try:
            self._ensure()
            vectors = get_rag_service().embedder.embed(segments)
        except Exception as e:
            logger.warning("intent_classifier_unavailable", error=str(e))
            return "general_inquiry", 0.0, []
        results = [self._best(v) for v in vectors]
        intent, score = max(results, key=lambda r: r[1])
        if score < MIN_CONFIDENCE:
            return "general_inquiry", round(score, 3), []
        found = []
        for label, s in results:
            if s >= SECONDARY_CONFIDENCE and label not in found:
                found.append(label)
        return intent, round(score, 3), found or [intent]

    def classify(self, text: str) -> Tuple[str, float]:
        intent, score, _ = self.classify_detailed(text)
        return intent, score


def mask_identifiers(text: str) -> str:
    """IDs like ORD002 carry no intent and distort embeddings; replace them with neutral words."""
    t = normalize_spoken_ids(text)
    t = re.sub(r"\b(?:ord(?:er)?)\s*(?:number|no\.?|#)?\s*\d+\b", "the order", t, flags=re.I)
    t = re.sub(r"\b(?:cust(?:omer)?)\s*(?:number|no\.?|id|#)?\s*\d+\b", "my account", t, flags=re.I)
    t = re.sub(r"\b(?:tkt|ticket)\s*(?:number|no\.?|#)?\s*\d+\b", "the ticket", t, flags=re.I)
    t = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "my email", t)
    return t


def sentiment_of(text: str) -> str:
    neg, pos = len(_NEGATIVE.findall(text)), len(_POSITIVE.findall(text))
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


TOOL_INTENTS = {
    "cancel_order": "order_cancellation",
    "create_return_request": "return_request",
    "get_order_status": "order_status",
    "get_delivery_estimate": "delivery_issue",
    "escalate_to_human": "escalation",
    "update_customer": "account_issue",
}

_classifier: Optional[IntentClassifier] = None


def get_intent_classifier() -> IntentClassifier:
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier()
    return _classifier
