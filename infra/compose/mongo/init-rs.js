// Khởi tạo replica set rs0 (chạy lại nhiều lần không sao).
// mongo1 có priority cao hơn để thường được bầu làm primary.
const cfg = {
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo1:27101", priority: 2 },
    { _id: 1, host: "mongo2:27102", priority: 1 },
    { _id: 2, host: "mongo3:27103", priority: 1 },
  ],
  settings: { electionTimeoutMillis: 10000 },
};

try {
  const st = rs.status();
  print(`rs0 đã khởi tạo, primary hiện tại: ${st.members.find(m => m.stateStr === "PRIMARY")?.name ?? "(đang bầu)"}`);
} catch (e) {
  if (e.codeName !== "NotYetInitialized") throw e;
  printjson(rs.initiate(cfg));
}

// Chờ có primary (tối đa 60 giây)
for (let i = 0; i < 60; i++) {
  const ok = rs.status().members.some(m => m.stateStr === "PRIMARY");
  if (ok) break;
  sleep(1000);
}
const st = rs.status();
st.members.forEach(m => print(`${m.name}  ${m.stateStr}`));
if (!st.members.some(m => m.stateStr === "PRIMARY")) {
  throw new Error("Không bầu được primary trong 60 giây");
}

// Ghi mặc định với majority để lệnh ghi đã xác nhận không mất khi chuyển primary
db.adminCommand({ setDefaultRWConcern: 1, defaultWriteConcern: { w: "majority" } });
print("rs0 sẵn sàng, defaultWriteConcern = majority");
