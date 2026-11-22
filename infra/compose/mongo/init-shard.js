// Chạy qua mongos: thêm 2 shard, bật sharding cho vietnam_gis, tạo các collection
// phân mảnh TRƯỚC khi nạp dữ liệu (mongorestore KHÔNG dùng --drop, nếu không
// collection đã shard sẽ bị xóa và tạo lại thành không shard).
// Chạy lại nhiều lần không sao.

const shards = [
  { name: "sh1", url: "sh1/shard1:27211" },
  { name: "sh2", url: "sh2/shard2:27212" },
];
const existing = db.adminCommand({ listShards: 1 }).shards.map(s => s._id);
for (const s of shards) {
  if (existing.includes(s.name)) print(`shard ${s.name} đã có`);
  else printjson(sh.addShard(s.url));
}

sh.enableSharding("vietnam_gis");
const gis = db.getSiblingDB("vietnam_gis");
const config = db.getSiblingDB("config");
// Time series được lưu dưới tên "system.buckets.<tên>" trong config.collections
const isSharded = ns => {
  const [dbName, ...rest] = ns.split(".");
  const ids = [ns, `${dbName}.system.buckets.${rest.join(".")}`];
  return config.collections.countDocuments({ _id: { $in: ids }, unsplittable: { $ne: true } }) > 0;
};

// 1) PDF gốc, collection lớn nhất (~1,1 GB): hashed để chia đều giữa các shard
if (!isSharded("vietnam_gis.hydromet_bulletin_files")) {
  printjson(sh.shardCollection("vietnam_gis.hydromet_bulletin_files", { source_file: "hashed" }));
}

// 2) Đỉnh/chân triều làm phẳng (time series), shard theo trạm rồi thời gian
if (!isSharded("vietnam_gis.tide_extremes")) {
  printjson(sh.shardCollection(
    "vietnam_gis.tide_extremes",
    { "meta.station": 1, ts: 1 },
    false,
    { timeseries: { timeField: "ts", metaField: "meta", granularity: "hours" } }
  ));
}

// Các collection còn lại nhỏ, để không shard (nằm ở primary shard của database).
print("Trạng thái sharding:");
sh.status();
