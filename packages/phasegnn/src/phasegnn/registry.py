"""Theo doi va dang ky mo hinh (checklist 6.6): MLflow, Optuna, collection `models`.

Ba nhip trong module nay:

1. `MlflowTracker` — ghi tham so, chi so, artifact cua mot lan huan luyen (co the tat
   MLflow: khi khong cai dat, chi ghi JSON/CSV cuc bo).
2. `optuna_search` — tim sieu tham so theo chieu MAE dinh ngay tren tap val, gioi han
   so lan thu theo thoi gian may (checklist 6.6).
3. `register_model` — ghi ban ghi vao collection `models` cua MongoDB voi
   `status: staging`, kem `hyperparams`, `metrics`, `data_snapshot` (checklist 6.6).

Tat ca deu "fail-soft": neu MongoDB/MLflow khong san sang, module chi ghi canh bao va
van tra ket qua huong dan nghiep vu.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

# ---------------------------------------------------------------------------
# 1. MLflow
# ---------------------------------------------------------------------------
class MlflowTracker:
    """Bọc MLflow theo kiểu "bat buoc fail-soft".

    Vi du::

        with MlflowTracker("phasegnn", seed=0, tracking_uri=None) as tr:
            tr.log_params({"lr": 1e-3})
            tr.log_metrics({"mae_peak": 0.12}, step=3)
            tr.log_artifact(out_dir / "train_log_seed0.json")
    """

    def __init__(self, run_name: str, *, params: dict | None = None,
                 tracking_uri: str | None = None, experiment: str = "phasegnn",
                 enabled: bool = True, log=print) -> None:
        self.run_name = run_name
        self.params = dict(params or {})
        self.tracking_uri = tracking_uri
        self.experiment = experiment
        self.enabled = bool(enabled)
        self.log = log
        self.run = None
        self._mlflow = None
        self._artifacts: list[Path] = []

    # -- vong doi ---------------------------------------------------------
    def __enter__(self) -> "MlflowTracker":
        if not self.enabled:
            return self
        try:
            import mlflow  # noqa: PLC0415
        except ImportError:
            self.log("[mlflow] khong cai dat -> bo qua ghi MLflow")
            self.enabled = False
            return self
        self._mlflow = mlflow
        try:
            if self.tracking_uri:
                mlflow.set_tracking_uri(self.tracking_uri)
            mlflow.set_experiment(self.experiment)
            self.run = mlflow.start_run(run_name=self.run_name)
            if self.params:
                mlflow.log_params(self.params)
        except Exception as exc:  # noqa: BLE001 - fail-soft
            self.log(f"[mlflow] khong ghi được run ({exc}) -> tiep tuc khong co MLflow")
            self.enabled = False
            self.run = None
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._mlflow is not None and self.run is not None:
            try:
                self._mlflow.end_run()
            except Exception:  # noqa: BLE001
                pass
        self.run = None

    # -- ghi ---------------------------------------------------------------
    def log_params(self, params: dict) -> None:
        self.params.update(params)
        if self.enabled and self.run is not None:
            try:
                self._mlflow.log_params(params)
            except Exception:  # noqa: BLE001
                pass

    def log_metrics(self, metrics: dict, step: int | None = None) -> None:
        if self.enabled and self.run is not None:
            try:
                self._mlflow.log_metrics(metrics, step=step)
            except Exception:  # noqa: BLE001
                pass

    def log_artifact(self, path: str | os.PathLike) -> None:
        p = Path(path)
        if not p.exists():
            return
        self._artifacts.append(p)
        if self.enabled and self.run is not None:
            try:
                self._mlflow.log_artifact(str(p))
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 2. Optuna — tim sieu tham so
# ---------------------------------------------------------------------------
@dataclass
class SearchSpace:
    """Khoang sieu tham so theo dac cuong 5.2.6.

    Gia tri mac dinh khop `TrainConfig`; khi Optuna khong chay, cac gia tri nay duoc
    dung luon.
    """

    lr: float = 1e-3
    weight_decay: float = 1e-4
    hidden_dim: int = 64
    num_gnn_layers: int = 2
    batch_size: int = 16
    lam1: float = 1.0
    lam2: float = 0.5
    lam3: float = 0.1
    lam4: float = 0.1

    def to_dict(self) -> dict:
        return asdict(self)

    def as_suggestions(self, trial) -> dict:
        """Đề xuất giá trị cho một `optuna.Trial`."""
        return {
            "lr": trial.suggest_float("lr", 1e-4, 5e-3, log=True),
            "weight_decay": trial.suggest_float("weight_decay", 1e-6, 1e-2, log=True),
            "hidden_dim": trial.suggest_categorical("hidden_dim", [32, 64, 128]),
            "num_gnn_layers": trial.suggest_int("num_gnn_layers", 1, 3),
            "batch_size": trial.suggest_categorical("batch_size", [8, 16, 32]),
            "lam1": trial.suggest_float("lam1", 0.0, 3.0),
            "lam2": trial.suggest_float("lam2", 0.0, 3.0),
            "lam3": trial.suggest_float("lam3", 0.0, 1.0),
            "lam4": trial.suggest_float("lam4", 0.0, 1.0),
        }


def optuna_search(objective: Callable[[dict], float], *, n_trials: int = 20,
                  time_budget_s: float = 1800.0, seed: int = 0,
                  space: SearchSpace | None = None, log=print) -> dict:
    """Tim sieu tham so bang Optuna, gioi han theo **thoi gian may**.

    Tham so
    -------
    objective : callable
        Nhan dict sieu tham so, tra ve gia tri **cang nho cang tot** (MAE dinh ngay
        tren tap val).
    n_trials : so lan thu toi da.
    time_budget_s : ngan sach thoi gian (giay) — dung de gioi han cong viec tren may
        khong co GPU.

    Tra ve dict `{"best_params": ..., "best_value": ..., "n_trials": ..., "history": [...]}`.
    """
    space = space or SearchSpace()
    try:
        import optuna
    except ImportError:
        log("[optuna] khong cai dat -> dung khoang mac dinh")
        return {"best_params": space.to_dict(), "best_value": float("nan"),
                "n_trials": 0, "history": [], "backend": "default"}

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler = optuna.samplers.TPESampler(seed=seed)
    history: list[dict] = []
    t0 = time.monotonic()

    def _objective(trial) -> float:
        if time.monotonic() - t0 > time_budget_s:
            raise optuna.TrialPruned()
        params = space.as_suggestions(trial)
        try:
            value = float(objective(params))
        except Exception as exc:  # noqa: BLE001
            log(f"[optuna] trial that bai: {exc}")
            raise optuna.TrialPruned() from exc
        history.append({"number": trial.number, "value": value, "params": params})
        log(f"[optuna] trial {trial.number}: MAE = {value:.5f}")
        return value

    study = optuna.create_study(direction="minimize", sampler=sampler)
    try:
        study.optimize(_objective, n_trials=n_trials, catch=(Exception,))
    except Exception as exc:  # noqa: BLE001
        log(f"[optuna] khong hoan tat nghien cuu ({exc})")
    best = study.best_trial if study.trials else None
    return {
        "best_params": best.params if best is not None else space.to_dict(),
        "best_value": float(best.value) if best is not None else float("nan"),
        "n_trials": len(history),
        "history": history,
        "backend": "optuna",
    }


# ---------------------------------------------------------------------------
# 3. Dang ky mo hinh vao collection `models`
# ---------------------------------------------------------------------------
@dataclass
class ModelRecord:
    """Ban ghi mo hinh theo `schemas/model/v1` (truong `models` cua MongoDB)."""

    model_id: str
    name: str
    variant: str
    status: str = "staging"          # staging | production | archived
    version: int = 1
    hyperparams: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    data_snapshot: dict = field(default_factory=dict)
    artifact_path: str | None = None
    train_config: dict = field(default_factory=dict)
    seed: int = 0
    created_at: float = field(default_factory=time.time)

    def to_mongo(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at
        return d


def register_model(record: ModelRecord, *, db=None, log=print) -> dict:
    """Ghi ban ghi mo hinh vao collection `models` (MongoDB).

    Neu khong co `db` (hoac khong ket noi duoc MongoDB), ham tra ve dict ban ghi de
    caller tu luu ra file — khong lam that bai luat trinh.
    """
    doc = record.to_mongo()
    if db is None:
        log("[registry] khong co MongoDB -> chi tra ve ban ghi (chua ghi DB)")
        return doc
    try:
        from vngis.db import get_db  # noqa: PLC0415
        from vngis.config import get_config  # noqa: PLC0415

        database = db if db is not None else get_db(get_config())
        col = database["models"]
        col.update_one({"_id": record.model_id}, {"$set": doc}, upsert=True)
        log(f"[registry] da ghi mo hinh '{record.model_id}' vao collection `models`")
    except Exception as exc:  # noqa: BLE001
        log(f"[registry] khong ghi duoc vao MongoDB ({exc})")
    return doc


def find_production_model(db=None, *, name: str | None = None) -> dict | None:
    """Lay ban ghi mo hinh dang `production` (dung cho job du bao hang ngay)."""
    try:
        from vngis.db import get_db  # noqa: PLC0415
        from vngis.config import get_config  # noqa: PLC0415

        database = db if db is not None else get_db(get_config())
        query = {"status": "production"}
        if name:
            query["name"] = name
        return database["models"].find_one(query, sort=[("version", -1)])
    except Exception:  # noqa: BLE001
        return None


def save_registry_json(doc: dict, out_dir: str | os.PathLike, filename: str = "model_record.json") -> Path:
    """Luu ban ghi mo hinh ra JSON (du phong khi khong co MongoDB)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")
    return path
