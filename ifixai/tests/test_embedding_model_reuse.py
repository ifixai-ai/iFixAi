from types import SimpleNamespace

from ifixai.evaluation import embedding_classifier as module
from ifixai.evaluation.response_classifier import ResponseClass


def test_default_model_is_loaded_once_per_classifier(monkeypatch):
    loads = []

    class Model:
        def __init__(self, name):
            loads.append(name)

        def encode(self, text):
            return SimpleNamespace(tolist=lambda: [1.0, 0.0])

    monkeypatch.setattr(module, "_sentence_transformers_available", True)
    monkeypatch.setattr(
        module, "_st_lib", SimpleNamespace(SentenceTransformer=Model), raising=False
    )
    classifier = module.EmbeddingClassifier(
        exemplars={ResponseClass.ANSWER: ["approved"]}
    )
    assert classifier.classify("first") == ResponseClass.ANSWER
    assert classifier.classify("second") == ResponseClass.ANSWER
    assert loads == ["all-MiniLM-L6-v2"]
