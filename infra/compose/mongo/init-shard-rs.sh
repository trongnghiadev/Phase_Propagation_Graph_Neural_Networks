#!/bin/sh
# Khởi tạo 3 replica set 1 node cho sharded cluster: cfgrs (config), sh1, sh2.
# Chạy lại nhiều lần không sao.
set -e

init_rs() {
  host="$1"; port="$2"; name="$3"; extra="$4"
  mongosh --quiet --host "$host" --port "$port" --eval "
    try { rs.status(); print('$name đã khởi tạo'); }
    catch (e) {
      if (e.codeName !== 'NotYetInitialized') throw e;
      printjson(rs.initiate({ _id: '$name' $extra, members: [{ _id: 0, host: '$host:$port' }] }));
    }
    for (let i = 0; i < 60; i++) {
      if (rs.status().members.some(m => m.stateStr === 'PRIMARY')) break;
      sleep(1000);
    }
    print('$name: ' + rs.status().members.map(m => m.name + ' ' + m.stateStr).join(', '));
  "
}

init_rs cfg1 27201 cfgrs ", configsvr: true"
init_rs shard1 27211 sh1 ""
init_rs shard2 27212 sh2 ""
echo "Đã khởi tạo cfgrs, sh1, sh2"
