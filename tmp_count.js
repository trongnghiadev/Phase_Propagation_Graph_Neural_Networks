db.getSiblingDB("vietnam_gis").getCollectionNames().forEach(function(c) {
  print(c + ": " + db.getSiblingDB("vietnam_gis")[c].countDocuments({}));
});
