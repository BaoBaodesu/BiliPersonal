"""v0.3 协议：保留网络主干，独立于 v0.2 工件。"""
import json
import threading
from pathlib import Path
import time
import uuid

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score
from backend.recommender.model import VideoRecommender, tf, keras
from backend.services.model_registry import atomic_json, digest
from backend.services.training_data import temporal_split, PROTOCOL, fingerprint


def metrics(labels, predictions, weights):
    labels, predictions, weights = map(np.asarray, (labels, predictions, weights))
    if not np.isfinite(predictions).all() or not len(labels):
        raise ValueError("无效模型预测")
    clipped = np.clip(predictions, 1e-7, 1 - 1e-7)
    losses = -(labels * np.log(clipped) + (1 - labels) * np.log(1 - clipped))
    order = np.argsort(-predictions, kind="stable")[:10]
    return {"loss": float(np.sum(losses * weights) / weights.sum()),
            "unweighted_loss": float(losses.mean()),
            "AP": float(average_precision_score(labels, predictions)) if len(np.unique(labels)) == 2 else None,
            "AUC": float(roc_auc_score(labels, predictions)) if len(np.unique(labels)) == 2 else None,
            "Precision@10": float(labels[order].mean()),
            "Recall@10": float(labels[order].sum() / labels.sum()) if labels.sum() else None}


class Network(VideoRecommender):
    def call(self, inputs):
        tags, author, quality = inputs
        tag_embeddings = self.tag_embedding(tags)
        author_embeddings = tf.expand_dims(self.author_embedding(author), 1)
        mask = tf.concat([tags != 0, tf.ones((tf.shape(tags)[0], 1), dtype=tf.bool)], axis=1)
        content = self.attention(tf.concat([tag_embeddings, author_embeddings], 1), mask=mask)
        quality = tf.cast(quality, tf.float32)
        wide = self.wide(quality)
        deep = self.deep_layer3(self.deep_layer2(self.deep_layer1(
            tf.concat([content, author_embeddings[:, 0, :], quality], 1))))
        return self.final_dense(tf.concat([wide, deep], 1))

    def neutral_rows(self):
        for layer in (self.tag_embedding, self.author_embedding):
            layer.embedding_matrix.assign(tf.concat(
                [tf.zeros_like(layer.embedding_matrix[:2]), layer.embedding_matrix[2:]], 0))


class Processor:
    def __init__(self, config):
        self.config = config
        self.tag2idx = config["tags"]
        self.author2idx = config["authors"]
        self.max_tags = config["max_tags"]

    @classmethod
    def fit(cls, samples, quality=False):
        videos = [s["video"] for s in samples]
        config = {"tags": {tag: i + 2 for i, tag in enumerate(sorted({t for v in videos for t in (v.get("tag") or v.get("tags") or [])}))},
                  "authors": {mid: i + 2 for i, mid in enumerate(sorted({str(v["mid"]) for v in videos if v.get("mid")}))},
                  "max_tags": max(1, min(32, max(len(v.get("tag") or v.get("tags") or []) for v in videos))),
                  "quality": quality}
        config["like_prior"] = float(sum(v["like"] for v in videos) / max(1, sum(v["view"] for v in videos)))
        config["favorite_prior"] = float(sum(v["favorite"] for v in videos) / max(1, sum(v["view"] for v in videos)))
        processor = cls(config)
        raw = np.array([processor.raw_quality(v) for v in videos])
        config["mean"] = raw.mean(0).tolist() if quality else [0.0]
        config["std"] = np.where(raw.std(0) > 1e-8, raw.std(0), 1).tolist() if quality else [1.0]
        return processor

    def raw_quality(self, video):
        counts = [float(video[k]) for k in ("view", "like", "favorite")]
        if not all(np.isfinite(counts)) or min(counts) < 0:
            raise ValueError("无效质量特征")
        view, like, favorite = counts
        if self.config["quality"]:
            return [np.log1p(view), (like + 100 * self.config["like_prior"]) / (view + 100),
                    (favorite + 100 * self.config["favorite_prior"]) / (view + 100)]
        log_view = np.log1p(view)
        return [float(np.clip((0.4 * like + 0.3 * favorite) / log_view + 0.3 * log_view / 20, 0, 1)) if log_view else 0.0]

    def transform(self, videos):
        tags = [[self.tag2idx.get(t, 1) for t in (v.get("tag") or v.get("tags") or [])[:self.max_tags]] or [1] for v in videos]
        return [np.asarray([t + [0] * (self.max_tags - len(t)) for t in tags], dtype=np.int32),
                np.asarray([self.author2idx.get(str(v.get("mid")), 1) for v in videos], dtype=np.int32),
                ((np.asarray([self.raw_quality(v) for v in videos]) - self.config["mean"]) / self.config["std"]).astype(np.float32)]


class Ranker:
    def __init__(self, model, processor):
        self.model, self.processor = model, processor
        self.max_tags = processor.max_tags
        self._lock = threading.Lock()

    def known_tags(self, tags):
        return [t for t in tags if t in self.processor.tag2idx]

    def score(self, videos):
        valid = [v for v in videos if all(v.get(k) is not None for k in ("view", "like", "favorite"))]
        if not valid:
            return []
        with self._lock:
            predictions = self.model(self.processor.transform(valid), training=False).numpy().ravel()
        if not np.isfinite(predictions).all():
            raise ValueError("预测包含非有限值")
        return [(v, float(p), self.known_tags(v.get("tag") or v.get("tags") or [])) for v, p in zip(valid, predictions)]

    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        processor = Processor(json.loads((directory / "processor.json").read_text(encoding="utf-8")))
        model = Network(len(processor.tag2idx) + 2, len(processor.author2idx) + 2, 32)
        model([np.zeros((1, processor.max_tags), np.int32), np.ones(1, np.int32),
               np.zeros((1, 3 if processor.config["quality"] else 1), np.float32)])
        model.load_weights(directory / "best_model.weights.h5")
        return cls(model, processor)

    @classmethod
    def train(cls, samples, registry, cutoff, quality=False, policy="base", callback=None, epochs=30, version=None):
        train, validation = temporal_split(samples)
        if len(train) < 20 or not validation or len({s["label"] for s in train}) < 2:
            raise ValueError("训练类别或样本不足，保留旧模型")
        version = version or "ranker-v03-" + uuid.uuid4().hex[:16]
        directory = registry.directory(version)
        directory.mkdir(parents=True)
        processor = Processor.fit(train, quality)
        atomic_json(directory / "processor.json", processor.config)
        atomic_json(directory / "samples.json", samples)
        x = processor.transform([s["video"] for s in train])
        labels = np.asarray([s["label"] for s in train], np.float32)
        weights = np.asarray([s["weight"] for s in train], np.float32)
        class_weights = {c: len(labels) / (2 * np.sum(labels == c)) for c in (0, 1)}
        model = Network(len(processor.tag2idx) + 2, len(processor.author2idx) + 2, 32)
        model(x)
        model.neutral_rows()
        optimizer = keras.optimizers.Adam(learning_rate=0.001)
        best, history = float("inf"), []
        validation_x = processor.transform([s["video"] for s in validation])
        for epoch in range(epochs):
            order = np.random.default_rng(42 + epoch).permutation(len(train))
            for start in range(0, len(train), 32):
                batch = order[start:start + 32]
                w = weights[batch] * np.asarray([class_weights[int(y)] for y in labels[batch]], np.float32)
                with tf.GradientTape() as tape:
                    predictions = model([v[batch] for v in x], training=True)
                    losses = keras.losses.binary_crossentropy(labels[batch, None], predictions)
                    loss = tf.reduce_sum(losses * w) / tf.reduce_sum(w)
                optimizer.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
                model.neutral_rows()
            result = metrics([s["label"] for s in validation], model(validation_x, training=False).numpy().ravel(),
                             [s["weight"] for s in validation])
            history.append(result)
            if result["loss"] < best:
                best = result["loss"]
                model.save_weights(directory / "best_model.weights.h5")
            if callback:
                callback(epoch + 1, epochs, float(loss), result)
        bundle = cls.load(directory)
        result = metrics([s["label"] for s in validation], bundle.model(validation_x, training=False).numpy().ravel(),
                         [s["weight"] for s in validation])
        metadata = {"version": version, "kind": "v03", "protocol": PROTOCOL, "data_cutoff_at": cutoff,
                    "data_hash": fingerprint(samples), "policy": policy, "quality": quality,
                    "created_at": time.time(), "train_bvids": [s["bvid"] for s in train],
                    "validation_bvids": [s["bvid"] for s in validation],
                    "metrics": result, "samples": len(samples), "train_samples": len(train),
                    "validation_samples": len(validation), "positives": int(sum(s["label"] for s in samples)),
                    "diagnostic_only": True, "missing_behavior_time": sum(s["event_at"] is None for s in samples),
                    "checksums": {name: digest(directory / name) for name in ("best_model.weights.h5", "processor.json", "samples.json")}}
        atomic_json(directory / "training.json", history)
        atomic_json(directory / "bundle.json", metadata)
        bundle.model_version, bundle.policy = version, policy
        return bundle, metadata
