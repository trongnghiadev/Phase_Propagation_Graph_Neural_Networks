"""Schema tường minh khi đọc `hydromet_bulletins` (chỉ các field G0 cần)."""
from __future__ import annotations

from pyspark.sql.types import (ArrayType, BooleanType, DoubleType, LongType, MapType,
                               StringType, StructField, StructType, TimestampType)

S, D, T, B = StringType(), DoubleType(), TimestampType(), BooleanType()

EXTREME = StructType([
    StructField("water_level_m", D),
    StructField("time", S),
    StructField("datetime", T),
    StructField("flag", S),
])

OBSERVED = StructType([
    StructField("station", S),
    StructField("high_tides", ArrayType(EXTREME)),
    StructField("low_tides", ArrayType(EXTREME)),
])

FORECAST = StructType([
    StructField("station", S),
    StructField("date", T),
    StructField("high_tides", ArrayType(EXTREME)),
    StructField("low_tides", ArrayType(EXTREME)),
])

RESERVOIR = StructType([
    StructField("reservoir", S),
    StructField("river", S),
    StructField("province", S),
    StructField("upstream_water_level_m", D),
    StructField("inflow_m3s", D),
    StructField("turbine_discharge_m3s", D),
    StructField("spillway_discharge_m3s", D),
    StructField("rainfall_mm", D),
    StructField("notes", S),
    StructField("flags", MapType(S, S)),
])

RAIN_STATION = StructType([
    StructField("station", S),
    StructField("station_raw", S),
    StructField("rainfall_mm", D),
    StructField("flag", S),
])

BULLETIN = StructType([
    StructField("source_file", S),
    StructField("bulletin_date", T),
    StructField("observation_date", T),
    StructField("source", StructType([
        StructField("is_primary", B),
        StructField("sha256", S),
        StructField("date_from_filename", T),
    ])),
    StructField("observed_water_levels", ArrayType(OBSERVED)),
    StructField("forecast_water_levels", ArrayType(FORECAST)),
    StructField("reservoirs", ArrayType(RESERVOIR)),
    StructField("rainfall", StructType([
        StructField("period", S),
        StructField("stations", ArrayType(RAIN_STATION)),
    ])),
])

STATION = StructType([
    StructField("code", S),
    StructField("name", S),
    StructField("kind", S),
    StructField("aliases", ArrayType(S)),
])

PROVINCE = StructType([
    StructField("era", S),
    StructField("code", LongType()),
    StructField("name_norm", S),
    StructField("aliases_norm", ArrayType(S)),
])
