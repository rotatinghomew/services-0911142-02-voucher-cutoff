from pathlib import Path
import json

from .domain import explore, validate
from .storage import Store, fingerprint
from .threshold import alternatives, estimate, prepare


class Workbench:
    def __init__(self, root, state):
        self.root = Path(root)
        self.product = json.loads((self.root / "product.json").read_text(encoding="utf-8"))
        self.store = Store(Path(state) / "research.sqlite3")
        initial = json.loads((self.root / "fixtures" / "data.json").read_text(encoding="utf-8"))
        validate(initial)
        self.default_revision = self.store.put_dataset("演示材料", initial)

    def context(self):
        return {"product": self.product, "default_revision": self.default_revision,
                "datasets": self.store.datasets(), "notebooks": self.store.notebooks()}

    def upload(self, body):
        data = body["data"]
        try:
            validate(data)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("字段结构不完整或类型不符") from error
        return {"revision": self.store.put_dataset(body.get("label", ""), data)}

    def inspect(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        result = explore(data, selection)
        return {"revision": revision, "selection": selection, "data_sha256": fingerprint(data), "view": result}

    def threshold(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", {})
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        result = estimate(data, selection)
        summary = result["summary"]
        resolved = {key: summary[key] for key in ("year", "window", "donut", "followup", "order", "kernel")}
        return {"analysis": "threshold", "revision": revision, "selection": resolved,
                "data_sha256": fingerprint(data), "threshold": result}

    def compare(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", {})
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        scenarios = alternatives(data, selection)
        options = prepare(data, selection)["options"]
        resolved = {key: options[key] for key in ("year", "window", "donut", "followup", "order", "kernel")}
        return {"analysis": "comparison", "revision": revision, "selection": resolved,
                "data_sha256": fingerprint(data), "comparison": {"scenarios": scenarios}}

    def save(self, body):
        analysis = body.get("analysis")
        if analysis == "threshold":
            result = self.threshold(body)
        elif analysis == "comparison":
            result = self.compare(body)
        elif analysis is None:
            result = self.inspect(body)
        else:
            raise ValueError("未知的分析类型：" + str(analysis))
        return self.store.save(body.get("title", ""), result["revision"], result["selection"], result)
