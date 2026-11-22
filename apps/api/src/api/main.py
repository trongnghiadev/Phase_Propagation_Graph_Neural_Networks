"""API tối giản cho cụm phân tán giả lập (khung ban đầu của apps/api).

  GET  /health/live          tiến trình còn sống (healthcheck Docker)
  GET  /health               trạng thái MongoDB replica set, Redis cache (Sentinel), các node khóa
  GET  /stations             danh sách trạm (đọc với readConcern majority qua URI)
  POST /lock/{resource}      thử lấy khóa Redlock, giữ hold_ms rồi nhả; trả fencing token

Khóa phân tán và fencing token dùng chung ở `vngis.locks`.
Ghi chú: các đường dẫn sẽ chuyển sang tiền tố /api/v1 khi có router chính thức (xem README).
"""
import os
import time

from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from redis.sentinel import Sentinel
from vngis.locks import Redlock, next_fence, parse_nodes

INSTANCE = os.getenv("INSTANCE", "api")
mongo = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=3000)
db = mongo[os.getenv("MONGO_DB", "vietnam_gis")]

sentinel = Sentinel(parse_nodes(os.environ["REDIS_SENTINELS"]), socket_timeout=0.5)
MASTER = os.getenv("REDIS_SENTINEL_MASTER", "cache")
redlock = Redlock(parse_nodes(os.environ["REDIS_LOCK_NODES"]))

app = FastAPI(title="gis-dist API", version="0.1")


@app.get("/health/live")
def live():
    return {"instance": INSTANCE, "ok": True}


@app.get("/health")
def health():
    out = {"instance": INSTANCE}
    try:
        st = mongo.admin.command("replSetGetStatus")
        out["mongo"] = {"set": st["set"], "members": {m["name"]: m["stateStr"] for m in st["members"]}}
    except Exception as e:  # noqa: BLE001 - báo lỗi thay vì sập endpoint
        out["mongo"] = {"error": str(e)[:200]}
    try:
        host, port = sentinel.discover_master(MASTER)
        out["redis_cache"] = {"master": f"{host}:{port}", "ping": sentinel.master_for(MASTER).ping()}
    except Exception as e:  # noqa: BLE001
        out["redis_cache"] = {"error": str(e)[:200]}
    out["lock_nodes"] = redlock.ping()
    return out


@app.get("/stations")
def stations():
    return list(db.stations.find({}, {"_id": 0, "code": 1, "name": 1, "kind": 1, "merit.COMID": 1}).sort("code", 1))


@app.post("/lock/{resource}")
def lock_demo(resource: str, ttl_ms: int = 5000, hold_ms: int = 200):
    lock, votes = redlock.acquire(f"lock:{resource}", ttl_ms)
    if lock is None:
        raise HTTPException(423, {"instance": INSTANCE, "acquired": False, "votes": votes})
    try:
        fence = next_fence(db, resource)
        time.sleep(hold_ms / 1000)
        return {"instance": INSTANCE, "acquired": True, "votes": votes,
                "validity_ms": round(lock.validity_ms), "fence": fence}
    finally:
        redlock.release(lock)
