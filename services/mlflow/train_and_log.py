"""
train_and_log.py — the real MLOps loop: train, register, promote, roll back
— and this version actually feeds a live service (agent-service), not just
the registry UI.

This script is NOT part of docker-compose. Run it manually, from your own
machine/venv, against a locally running MLflow tracking server:

    docker compose up -d mlflow postgres    # starts the server + its backend DB
    pip install -r requirements.txt
    python train_and_log.py

It exists to answer one concrete question a lot of teams don't have an
answer to: "if the new model is bad, how do I get back to the old one?" —
and to make that concrete by training something a real request path
actually uses: `agent-service` classifies every incoming chat message's
*urgency* using whichever version is currently tagged `champion` in
MLflow's registry (see `services/agent-service/app/model_registry.py`).
Flip the alias here, and agent-service's live behavior changes within its
next cache refresh (default 60s) or immediately via `POST
localhost:8003/admin/reload-model` — with zero redeploy, zero code change.

WHAT IT DOES
------------
1. Trains a classifier that combines message TEXT (TF-IDF) with LIVE
   customer-context FEATURES from Feast's online store (engagement_score,
   days_since_last_contact, total_conversations, open_tickets — see
   `services/feature-store/`) via a `ColumnTransformer` + Logistic
   Regression, wrapped as an `mlflow.pyfunc.PythonModel` so agent-service
   can call `.predict(df)` with a one-row DataFrame built by
   `app/model_registry.py` (text + a live `app/feature_store_client.py`
   lookup). This is what makes services/feature-store/ a REAL input to a
   live model instead of a standalone lesson: the same "urgent"-sounding
   message classifies differently depending on which customer sent it —
   see `services/feature-store/README.md` for the concrete before/after.
   Trained on a small labeled set of Onwly-support-style messages —
   "urgent" vs "normal" — and logs it to MLflow as run #1 ("baseline").
2. Registers it as `support-urgency-classifier` version 1 and sets the
   `champion` alias on it. This IS "setting a baseline": champion always
   points at whichever version is currently production-ready, and
   agent-service always asks for `models:/support-urgency-classifier@champion`,
   never a hardcoded version number.
3. Trains a SECOND run deliberately on a tiny, skewed slice of the data
   (a realistic way a real regression happens: someone re-trains on a bad
   data pull) — logs + registers it as version 2 ("new candidate").
4. "Promotes" v2 by moving the `champion` alias from v1 -> v2.
5. Compares held-out accuracy, detects the regression, and ROLLS BACK by
   moving `champion` back to v1 — a one-line `set_registered_model_alias`
   call, not a git revert + redeploy.

Every step prints a clear narration line so you can follow along without
the MLflow UI open — though you should also open http://localhost:5050
(Models -> support-urgency-classifier -> Aliases) to see the same story
there, and hit agent-service's `/chat` before/after each alias flip (see
`README.md`'s MLOps exercise) to see the live effect.
"""

import os

import mlflow
import mlflow.pyfunc
import pandas as pd
import sklearn
from mlflow import MlflowClient
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline

MODEL_NAME = "support-urgency-classifier"
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5050")

# Must match services/feature-store/feature_repo/features.py's schema and
# services/agent-service/app/feature_store_client.py's DEFAULT_FEATURES keys
# exactly -- this is the live train/serve contract. If you add a feature
# here, add it in both of those places too.
NUMERIC_FEATURES = ["engagement_score", "days_since_last_contact", "total_conversations", "open_tickets"]

# Fictional Onwly support messages (same fictional product used by
# rag-service's seeded knowledge base) labeled by urgency. Deliberately
# small and hand-labeled -- realistic for a first-pass internal classifier,
# not a production-scale dataset.
URGENT_MESSAGES = [
    "This is the third time your product has broken and I want a refund right now, this is ridiculous!",
    "Our entire team is locked out and we have a client demo in 10 minutes, please help immediately!",
    "URGENT: payment failed and our workspace got downgraded, we need this fixed today.",
    "I've been waiting 3 days for a response and nobody has helped me, this is unacceptable.",
    "The system is down for all our agents right now, we're losing tickets by the minute.",
    "You charged us twice this month and support hasn't responded in 48 hours, escalate this now.",
    "Everything crashed during our biggest sales call of the quarter, I need someone on the phone NOW.",
    "This is a legal matter now, our data was exposed and I need a callback within the hour.",
    "Still broken after your last three 'fixes' -- I am done waiting, cancel my subscription today.",
    "My whole team can't log in an hour before a customer deadline, please treat this as critical.",
    "We are losing money every minute this outage continues, please escalate immediately.",
    "I need this refunded today or I am disputing the charge with my bank.",
    # Added to bring the held-out test set from 8 to 12 rows (see the
    # updated holdout slicing in main()) -- a single misclassification on
    # an 8-row test set swings "accuracy" by ~12.5 points, which is a lot
    # of noise to hang a promotion/rollback decision on even for a teaching
    # example. Still a small, fully hand-labeled set on purpose, not a
    # production-scale dataset -- see the module docstring.
    "This is completely unacceptable, three outages this week and no explanation from support.",
    "We need this escalated NOW, our compliance audit is in an hour and the export tool is broken.",
]

NORMAL_MESSAGES = [
    "How do I reset my password?",
    "Can you tell me about your pricing plans?",
    "What's the difference between the Team and Business plans?",
    "How do I invite a new team member to my workspace?",
    "Just checking if the Slack integration supports two-way replies.",
    "Is there a way to export my tickets to CSV?",
    "Do you have an API for creating tickets programmatically?",
    "What happens to my data if I downgrade to the Free plan?",
    "Can I change my workspace subdomain after signup?",
    "How many people can I add on the free plan?",
    "Where can I find the webhook settings?",
    "Just wanted to say the new dashboard looks great, nice work!",
    # Added alongside URGENT_MESSAGES' two new entries -- see that list's
    # comment for why (bigger, less noisy held-out test set).
    "Does the mobile app support push notifications for new tickets?",
    "Just curious, is there a dark mode planned for the dashboard?",
]

# Deliberately AMBIGUOUS phrasing -- on text alone these could reasonably go
# either way. This is the whole point of combining text with live
# customer-context features from Feast (see
# ../feature-store/README.md and ../agent-service/app/feature_store_client.py)
# instead of classifying from text alone: each one is trained TWICE, once
# paired with a healthy customer's features (-> normal) and once with an
# at-risk customer's features (-> urgent). A model trained on text alone
# cannot do better than a coin flip on these; a model that's actually
# learned to use the numeric features should get them right.
BORDERLINE_MESSAGES = [
    "This still hasn't been resolved, can someone take a look?",
    "Following up again on my open ticket.",
    "I'm getting a bit frustrated with how long this is taking.",
    "Any update on the issue I reported earlier?",
]

# Three feature presets used to attach plausible customer-context values to
# every training example. Clear-cut urgent/normal text gets NEUTRAL features
# (so the model still learns strong text signal dominates for the easy
# cases); the ambiguous BORDERLINE_MESSAGES get GOOD (-> normal) and BAD
# (-> urgent) explicitly, which is what actually teaches the combined signal.
GOOD_CUSTOMER = {"engagement_score": 0.9, "days_since_last_contact": 1, "total_conversations": 20, "open_tickets": 0}
BAD_CUSTOMER = {"engagement_score": 0.15, "days_since_last_contact": 25, "total_conversations": 3, "open_tickets": 4}
NEUTRAL_CUSTOMER = {"engagement_score": 0.5, "days_since_last_contact": 7, "total_conversations": 10, "open_tickets": 1}


def build_training_frame(urgent_texts, normal_texts, borderline_texts) -> pd.DataFrame:
    """Builds the combined text+features training frame. Every row has the
    same shape agent-service sends at inference time: one `text` column
    plus the 4 NUMERIC_FEATURES columns (see app/model_registry.py)."""
    rows = []
    for t in urgent_texts:
        rows.append({"text": t, **NEUTRAL_CUSTOMER, "label": "urgent"})
    for t in normal_texts:
        rows.append({"text": t, **NEUTRAL_CUSTOMER, "label": "normal"})
    for t in borderline_texts:
        rows.append({"text": t, **GOOD_CUSTOMER, "label": "normal"})
        rows.append({"text": t, **BAD_CUSTOMER, "label": "urgent"})
    return pd.DataFrame(rows)


def build_pipeline() -> Pipeline:
    # ColumnTransformer combines a text branch (TF-IDF over the `text`
    # column) with the numeric customer-context features (passed through
    # unscaled -- logistic regression handles the differing ranges fine at
    # this dataset size) into one feature vector for LogisticRegression.
    # Selecting "text" as a bare column name (not ["text"]) is what makes
    # ColumnTransformer hand TfidfVectorizer a 1D iterable of strings
    # instead of a 2D DataFrame slice, which is what it expects.
    preprocessor = ColumnTransformer(
        transformers=[
            ("text", TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=1), "text"),
            ("numeric", "passthrough", NUMERIC_FEATURES),
        ]
    )
    return Pipeline(
        [
            ("features", preprocessor),
            ("clf", LogisticRegression(max_iter=1000)),
        ]
    )


class UrgencyClassifier(mlflow.pyfunc.PythonModel):
    """Thin pyfunc wrapper so `.predict(df)` returns plain string labels.
    `model_input` is expected to be a pandas DataFrame with a `text` column
    plus NUMERIC_FEATURES columns -- exactly what agent-service's
    app/model_registry.py builds on every /chat turn."""

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline

    def predict(self, context, model_input, params=None):
        if not hasattr(model_input, "columns"):
            raise ValueError(
                "UrgencyClassifier expects a DataFrame with columns: text, " + ", ".join(NUMERIC_FEATURES)
            )
        return list(self.pipeline.predict(model_input))


def train_and_log_run(run_name, train_df, test_df):
    """Train one urgency classifier on a combined text+features DataFrame,
    log params/metrics/model to MLflow, and return (run_id, accuracy).
    `train_df`/`test_df` must have a `label` column plus everything
    build_training_frame() produces."""
    with mlflow.start_run(run_name=run_name) as run:
        feature_cols = ["text"] + NUMERIC_FEATURES
        pipeline = build_pipeline()
        pipeline.fit(train_df[feature_cols], train_df["label"])
        preds = pipeline.predict(test_df[feature_cols])
        acc = accuracy_score(test_df["label"], preds)

        # Per-class precision/recall, not just accuracy -- for an urgency
        # classifier specifically, the two error types are NOT equally
        # costly: missing a genuinely urgent message (a false negative on
        # the "urgent" class -- i.e. low recall_urgent) means a customer in
        # real distress gets a normal-priority response, which is worse
        # than the reverse mistake (an over-cautious escalation on a
        # message that wasn't actually urgent). A single "accuracy" number
        # can hide a model that's specifically bad at the costlier error --
        # logging both per-class numbers is what would actually let you
        # set a promotion gate on the metric that matters, instead of on
        # the metric that's easiest to compute.
        labels = ["urgent", "normal"]
        precision, recall, f1, _ = precision_recall_fscore_support(
            test_df["label"], preds, labels=labels, zero_division=0
        )

        mlflow.log_param("n_training_examples", len(train_df))
        mlflow.log_param("features", ",".join(feature_cols))
        mlflow.log_metric("accuracy", acc)
        for label, p, r, f in zip(labels, precision, recall, f1):
            mlflow.log_metric(f"precision_{label}", p)
            mlflow.log_metric(f"recall_{label}", r)
            mlflow.log_metric(f"f1_{label}", f)

        mlflow.pyfunc.log_model(
            python_model=UrgencyClassifier(pipeline),
            artifact_path="model",
            registered_model_name=MODEL_NAME,
            # Exact-pinned, matching requirements.txt (this file's own
            # deps) exactly, not a bare unpinned package-name list -- the
            # real bug this fixes: an unpinned list here means whatever
            # version happens to be installed wherever this model is later
            # LOADED gets used for deserialization, which is exactly how
            # a real, observed `InconsistentVersionWarning: ... from
            # version 1.7.0 when using version 1.9.0` happened. Pinning the
            # exact training-time versions here means anyone (or anything,
            # like agent-service) loading this model artifact knows
            # precisely what to install to match it, instead of guessing.
            pip_requirements=[
                f"scikit-learn=={sklearn.__version__}",
                f"mlflow=={mlflow.__version__}",
                f"pandas=={pd.__version__}",
            ],
        )

        recall_urgent = dict(zip(labels, recall))["urgent"]
        print(
            f"  logged run '{run_name}' (run_id={run.info.run_id}) accuracy={acc:.4f} "
            f"recall_urgent={recall_urgent:.4f} on {len(train_df)} examples"
        )
        return run.info.run_id, acc


def get_latest_version_for_run(client, model_name, run_id):
    for mv in client.search_model_versions(f"name='{model_name}'"):
        if mv.run_id == run_id:
            return mv.version
    raise RuntimeError(f"Could not find registered version for run_id={run_id}")


def main():
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("support-urgency-classifier")
    client = MlflowClient(tracking_uri=TRACKING_URI)

    # Fixed, disjoint held-out test set: last 5 of each clear-cut class,
    # PLUS the last BORDERLINE_MESSAGES entry (both its good- and
    # bad-customer variants, held out from training). That last part is
    # what actually tests whether the model learned to use the numeric
    # features rather than just memorizing text -- a text-only model
    # cannot beat chance on it, since the same held-out sentence appears
    # once labeled "normal" and once labeled "urgent". 5 (not 3) per class
    # -- a 12-row test set instead of 8 means one misclassification swings
    # accuracy by ~8 points instead of ~12.5, still small but less noisy.
    test_urgent, train_urgent = URGENT_MESSAGES[-5:], URGENT_MESSAGES[:-5]
    test_normal, train_normal = NORMAL_MESSAGES[-5:], NORMAL_MESSAGES[:-5]
    test_borderline, train_borderline = BORDERLINE_MESSAGES[-1:], BORDERLINE_MESSAGES[:-1]

    train_df_full = build_training_frame(train_urgent, train_normal, train_borderline)
    test_df = build_training_frame(test_urgent, test_normal, test_borderline)

    # --- Step 1: BASELINE ---------------------------------------------------
    print("\nSTEP 1: training baseline model (run 1) on the full training set")
    v1_run_id, v1_acc = train_and_log_run("baseline-v1", train_df_full, test_df)
    v1_version = get_latest_version_for_run(client, MODEL_NAME, v1_run_id)
    client.set_registered_model_alias(MODEL_NAME, "champion", v1_version)
    print(f"BASELINE: v{v1_version} registered and tagged champion (accuracy={v1_acc:.4f})")
    print(f"  -> agent-service will pick this up within its refresh window, or immediately via POST /admin/reload-model")

    # --- Step 2: NEW CANDIDATE (a realistic regression: trained on a bad,
    # skewed data pull that also happens to drop every borderline example --
    # e.g. someone re-trained on a data pull that filtered out the rows
    # carrying the customer-context signal entirely, not just a class
    # imbalance) ------------------------------------------------------------
    print("\nSTEP 2: training a new candidate (run 2) on a deliberately skewed slice of data")
    skewed_df = build_training_frame(train_urgent[:1], train_normal, [])  # almost all "normal", no borderline examples at all
    v2_run_id, v2_acc = train_and_log_run("candidate-v2-skewed-data", skewed_df, test_df)
    v2_version = get_latest_version_for_run(client, MODEL_NAME, v2_run_id)
    print(f"NEW CANDIDATE: v{v2_version} registered (accuracy={v2_acc:.4f})")

    # --- Step 3: PROMOTE -----------------------------------------------------
    print("\nSTEP 3: promoting the new candidate to champion")
    print(f"PROMOTING v{v2_version} to champion")
    client.set_registered_model_alias(MODEL_NAME, "champion", v2_version)
    print("  -> try POST localhost:8003/admin/reload-model then localhost:8003/chat with an angry message NOW")
    print("     -- the skewed model likely mislabels it 'normal'. That's the live effect of a bad promotion.")

    # --- Step 4: DETECT REGRESSION AND ROLL BACK -----------------------------
    print("\nSTEP 4: comparing candidate vs baseline accuracy to decide whether to keep it")
    print(f"  v{v1_version} (previous champion) accuracy = {v1_acc:.4f}")
    print(f"  v{v2_version} (current champion)  accuracy = {v2_acc:.4f}")

    if v2_acc < v1_acc:
        print(f"REGRESSION DETECTED, ROLLING BACK: champion -> v{v1_version}")
        client.set_registered_model_alias(MODEL_NAME, "champion", v1_version)
        final_champion = v1_version
    else:
        print(f"No regression detected, keeping v{v2_version} as champion")
        final_champion = v2_version

    champion_mv = client.get_model_version_by_alias(MODEL_NAME, "champion")
    print(
        f"\nFINAL STATE: '{MODEL_NAME}' champion alias -> version {champion_mv.version} "
        f"(run_id={champion_mv.run_id})"
    )
    print(f"Open {TRACKING_URI} -> Models -> {MODEL_NAME} -> Aliases to see this in the UI.")
    print("Call POST localhost:8003/admin/reload-model to make agent-service pick up the rollback immediately.")
    assert str(final_champion) == str(champion_mv.version)


if __name__ == "__main__":
    main()
