"""
推荐器：对原版 app/Recommender.py 的拆分。
- 模型结构、特征处理、训练流程（30 epoch、class_weight、保存最佳模型、训练曲线）与原版一致。
- 拆成 train() / load() / score() 三部分：训练在后台执行，已训练的模型可直接从 saved_model 加载。
- 候选池、去重、过滤等逻辑移到 services/recommendation_service.py。
"""

import json
import threading

from backend.config import HISTORY_PATH
from backend.recommender.model import *


class Recommender:
    def __init__(self, model, processor, max_tags):
        self.model = model
        self.processor = processor
        self.max_tags = max_tags
        self._lock = threading.Lock()

    @classmethod
    def train(cls, history_path=HISTORY_PATH, progress_callback=None):
        """
        与原版 Recommender.__init__ 相同的训练流程。
        progress_callback(epoch, num_epochs, loss, metrics) 用于上报训练进度。
        """
        processor = FeatureProcessor()
        processed_data, labels, max_tags = load_and_process_data(history_path, processor)
        class_weights_array = class_weight.compute_class_weight(
            class_weight="balanced", classes=np.unique(labels), y=labels
        )
        class_weights_dict = {i: weight for i, weight in enumerate(class_weights_array)}
        print(f"类别权重: {class_weights_dict}")

        num_tags = len(processor.tag2idx)
        num_authors = len(processor.author2idx)
        embedding_dim = 32

        model = VideoRecommender(num_tags, num_authors, embedding_dim)
        optimizer = keras.optimizers.Adam(learning_rate=0.001)

        num_epochs = 30

        best_loss = float("inf")
        losses = []
        AUCROC = []
        AveragePrecision = []
        PrecisionAtK = []
        RecallAtK = []
        metrics = {}

        for epoch in range(num_epochs):
            print(f"\nEpoch {epoch + 1}/{num_epochs}")

            loss = train_model(model, processed_data, labels, optimizer, class_weights_dict)
            losses.append(loss)
            print(f"Epoch {epoch + 1}, Average Loss: {loss}")

            metrics = evaluate_model(model, processed_data, labels)
            print(f"评估指标: {metrics}")

            AUCROC.append(metrics["AUC-ROC"])
            AveragePrecision.append(metrics["Average Precision"])
            PrecisionAtK.append(metrics["Precision@k"])
            RecallAtK.append(metrics["Recall@k"])

            if loss < best_loss:
                best_loss = loss
                print(f"在 epoch {epoch + 1} 保存最佳模型")
                save_model_and_processor(model, processor, save_dir)

            if progress_callback:
                progress_callback(epoch + 1, num_epochs, float(loss), metrics)

        # 原版每个 epoch 都重绘一次，这里只在结束时绘制一次，结果相同
        plt.figure(figsize=(10, 6))
        plt.plot(range(1, len(losses) + 1), losses, "b-", label="Training Loss")
        plt.plot(range(1, len(AUCROC) + 1), AUCROC, "r-", label="AUC-ROC")
        plt.plot(range(1, len(AveragePrecision) + 1), AveragePrecision, "g-", label="Average Precision")
        plt.plot(range(1, len(PrecisionAtK) + 1), PrecisionAtK, "y-", label="Precision@k")
        plt.plot(range(1, len(RecallAtK) + 1), RecallAtK, "c-", label="Recall@k")
        plt.title("Training Metrics")
        plt.xlabel("Epoch")
        plt.ylabel("Loss")
        plt.legend()
        plt.grid(True)
        plt.savefig(os.path.join(save_dir, "training_loss.png"))
        plt.close()

        summary = {
            "samples": int(len(labels)),
            "positives": int(labels.sum()),
            "num_tags": num_tags,
            "num_authors": num_authors,
            "best_loss": float(best_loss),
            "final_metrics": {k: float(v) for k, v in metrics.items()},
        }
        # 与原版一致：训练结束后使用内存中最后一个 epoch 的模型进行推荐
        return cls(model, processor, max_tags), summary

    @classmethod
    def load(cls, max_tags):
        model, processor = load_model_and_processor(save_dir)
        return cls(model, processor, max_tags)

    def known_tags(self, tags):
        return [tag for tag in tags if tag in self.processor.tag2idx]

    def score(self, videos):
        """
        与原版 recommend() 的评分部分一致：
        与训练集 tag2idx 无交集的候选被丢弃；未知作者映射为 0；按 max_tags 补零。
        返回 [(video, rating, matched_tags)]，未排序。
        """
        processed_videos = []
        for video in videos:
            valid_tags = self.known_tags(video.get("tag") or [])
            if not valid_tags:
                continue
            processed_videos.append((video, valid_tags))
        if not processed_videos:
            return []

        all_tags = []
        all_authors = []
        all_quality_scores = []
        for video, valid_tags in processed_videos:
            tags = [self.processor.tag2idx[tag] for tag in valid_tags][: self.max_tags]
            tags = tags + [0] * (self.max_tags - len(tags))
            author_idx = (
                self.processor.author2idx[video["author"]]
                if video["author"] in self.processor.author2idx
                else 0
            )
            quality_score = self.processor.calculate_quality_score(
                video.get("view") or 0, video.get("like") or 0, video.get("favorite") or 0
            )
            all_tags.append(tags)
            all_authors.append(author_idx)
            all_quality_scores.append(quality_score)

        with self._lock:
            predictions = (
                self.model(
                    [
                        np.array(all_tags, dtype=np.int32),
                        np.array(all_authors, dtype=np.int32),
                        np.array(all_quality_scores, dtype=np.float32),
                    ]
                )
                .numpy()
                .flatten()
            )
        return [(video, float(pred), valid_tags) for (video, valid_tags), pred in zip(processed_videos, predictions)]


def read_history(history_path=HISTORY_PATH):
    if not os.path.exists(history_path):
        return []
    with open(history_path, "r", encoding="utf-8") as f:
        return json.load(f)
