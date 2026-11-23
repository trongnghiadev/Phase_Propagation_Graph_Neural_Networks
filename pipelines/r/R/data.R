# R/data.R -- Nap du lieu dung chung cho cac script R (GĐ4).
#
# Tất cả logic dung chung (không lặp lại) nằm ở đây và trong `R/connect.R`,
# `R/io.R`, `R/harmonics.R`, `R/theme.R`.

# ---------------------------------------------------------------------------
# Tham so chung
# ---------------------------------------------------------------------------
G5_DIR <- "data/processed/features/g5"
GRAPH_DIR <- "data/processed/graph"

# Các trạm triều (không phải trạm mưa / hồ chứa).
TIDE_STATIONS <- c("VUNG_TAU", "NHA_BE", "PHU_AN", "THU_DAU_MOT", "BIEN_HOA",
                   "DAU_TIENG", "TAN_AN", "BEN_LUC", "GO_DAU_HA")

# Trạm dùng làm "trạm tham chiếu" (biển) cho phân tích độ trễ pha.
REFERENCE_STATION <- "VUNG_TAU"

# ---------------------------------------------------------------------------
# Nap bang dieu hoa / tap huấn luyện
# ---------------------------------------------------------------------------
# Doc tap huấn luyện G5 (Parquet phan vung theo nam) -> data.frame.
load_g5 <- function(root = NULL) {
  root <- root %||% repo_root()
  path <- file.path(root, G5_DIR)
  if (!dir.exists(path)) {
    stop("Khong tim thay tap huấn luyện G5 tai: ", path,
         "\nHay chay job Spark `pipelines/spark/features/build_training_set.py` truoc.", call. = FALSE)
  }
  df <- read_parquet_df(path)
  df$day <- as.character(df$day)
  df$year <- as.integer(df$year)
  df
}

# Doc do thi (canh + nut).
load_graph <- function(root = NULL) {
  root <- root %||% repo_root()
  edges <- read_parquet_df(file.path(root, GRAPH_DIR, "edges.parquet"))
  nodes <- read_parquet_df(file.path(root, GRAPH_DIR, "nodes.parquet"))
  list(edges = edges, nodes = nodes)
}

# ---------------------------------------------------------------------------
# Nap cac bang phan tich tu MongoDB
# ---------------------------------------------------------------------------
# Ban tin chinh: dung de gan "ngay quan sat" cho dinh/chan trieu.
# Chi giu ban tin `tide_forecast_5day` va `source.is_primary = TRUE`.
load_bulletins <- function(url = NULL, db = NULL) {
  hb <- mongo_find("hydromet_bulletins",
                   query = '{"source.is_primary": true, "bulletin_type": "tide_forecast_5day"}',
                   url = url, db = db)
  if (!nrow(hb)) return(hb)
  hb$source_file <- as.character(hb$source_file)
  hb$observation_day <- vn_day(hb$observation_date)
  hb$bulletin_day <- vn_day(hb$bulletin_date)
  hb
}

# Goc tao bang tra cua "ngay quan sat" -> file ban tin.
# `tide_extremes.source_file` tro toi `hydromet_bulletins.source_file`.
observation_day_map <- function(bulletins) {
  if (!nrow(bulletins)) return(stats::setNames(character(0), character(0)))
  ok <- !is.na(bulletins$observation_day)
  stats::setNames(bulletins$observation_day[ok], bulletins$source_file[ok])
}

# ---------------------------------------------------------------------------
# Nap `tide_extremes` va dua ve dang bang phang
# ---------------------------------------------------------------------------
# Tra ve data.frame: station, kind, ts (POSIXct gio Viet Nam), ts_day, value,
#                    qc, qc_flag, source_file, obs_day, day_offset.
#
# LOC: `qc = "ok"` va `qc_flag != "suspect"` -- cung quy tac voi
# `pipelines/spark/features/build_training_set.py`.
load_tide_extremes <- function(bulletins = NULL, url = NULL, db = NULL,
                               keep_qc = "ok", drop_suspect = TRUE) {
  te <- mongo_find("tide_extremes", url = url, db = db)
  if (!nrow(te)) return(te)
  te$station <- te$meta$station
  te$kind    <- te$meta$kind
  te$ts      <- to_vn(te$ts)
  te$ts_day  <- format(te$ts, "%Y-%m-%d", tz = TZ_VN)
  te$value   <- suppressWarnings(as.numeric(te$value))
  te$qc      <- as.character(te$qc)
  te$qc_flag <- ifelse(is.null(te$qc_flag), NA_character_, as.character(te$qc_flag))

  if (!is.null(keep_qc)) {
    te <- te[!is.na(te$qc) & te$qc %in% keep_qc, , drop = FALSE]
  }
  if (drop_suspect) {
    te <- te[is.na(te$qc_flag) | te$qc_flag != "suspect", , drop = FALSE]
  }

  if (is.null(bulletins)) bulletins <- load_bulletins(url = url, db = db)
  omap <- observation_day_map(bulletins)
  te$obs_day <- unname(omap[te$source_file])
  # Ban tin khong tim thay -> dung ngay lich cua `ts` (fallback, duoc ghi ro)
  te$obs_day[is.na(te$obs_day)] <- te$ts_day[is.na(te$obs_day)]
  te$obs_day <- as.character(te$obs_day)
  te$day_offset <- mapply(peak_day_offset, te$obs_day, te$ts_day)
  rownames(te) <- NULL
  te
}

# ---------------------------------------------------------------------------
# Nap `official_forecasts` (du bao KTTV) -> bang phang
# ---------------------------------------------------------------------------
# Bang pham: station, issued_date, target_date, lead_day, kind, ts, value, qc.
# `ts` la gio Viet Nam.
load_official_forecasts <- function(url = NULL, db = NULL) {
  of <- mongo_find("official_forecasts", url = url, db = db)
  if (!nrow(of)) return(of)
  # tach `high`/`low` (list) thanh cac dong phang
  hl <- list()
  for (i in seq_len(nrow(of))) {
    for (kind in c("high", "low")) {
      d <- of[[kind]][[i]]
      if (is.null(d) || !nrow(d)) next
      hl[[length(hl) + 1L]] <- data.frame(
        station = of$station[i], issued_date = of$issued_date[i],
        target_date = of$target_date[i], lead_day = of$lead_day[i],
        kind = kind, ts = to_vn(d$ts), value = as.numeric(d$value),
        qc = as.character(d$qc), stringsAsFactors = FALSE
      )
    }
  }
  if (!length(hl)) return(NULL)
  out <- do.call(rbind, hl)
  out$issued_date <- as.character(out$issued_date)
  out$target_date <- as.character(out$target_date)
  out$hour <- vn_hour(out$ts)
  rownames(out) <- NULL
  out
}

# ---------------------------------------------------------------------------
# Nap `reservoir_daily` va `rainfall_daily`
# ---------------------------------------------------------------------------
load_reservoir_daily <- function(url = NULL, db = NULL) {
  rd <- mongo_find("reservoir_daily", url = url, db = db)
  if (!nrow(rd)) return(rd)
  rd$day <- as.character(rd$day)
  rd$date <- to_vn(rd$date)
  for (cc in c("inflow_m3s", "turbine_discharge_m3s", "spillway_discharge_m3s",
               "upstream_water_level_m", "rainfall_mm")) {
    if (!is.null(rd[[cc]])) rd[[cc]] <- suppressWarnings(as.numeric(rd[[cc]]))
  }
  rd
}

load_rainfall_daily <- function(url = NULL, db = NULL) {
  rf <- mongo_find("rainfall_daily", url = url, db = db)
  if (!nrow(rf)) return(rf)
  rf$day <- as.character(rf$day)
  rf$date <- to_vn(rf$date)
  rf$rainfall_mm <- suppressWarnings(as.numeric(rf$rainfall_mm))
  rf
}

# ---------------------------------------------------------------------------
# Bang trạm: toạ độ, khoảng cách dọc sông, ngưỡng báo động theo thời gian
# ---------------------------------------------------------------------------
load_stations <- function(url = NULL, db = NULL) {
  st <- mongo_find("stations", url = url, db = db)
  if (!nrow(st)) return(st)
  coords <- do.call(rbind, lapply(st$location$coordinates, function(cc) {
    if (is.null(cc) || length(cc) < 2L) c(NA_real_, NA_real_) else as.numeric(cc[1:2])
  }))
  st$lon <- coords[, 1]; st$lat <- coords[, 2]
  st$dist_to_root_km <- st$merit$dist_to_root_km
  st$merit_COMID <- st$merit$COMID
  st
}

# Bang nguong bao dong theo thoi gian cho tung tram.
# Tra ve data.frame: station, from_day, to_day, level_1_m, level_2_m, level_3_m.
#
# QUY TAC: dung `stations.alarm_history` (nhieu doan, moi doan co `from`/`to`).
# KHONG dung nguong co dinh. Doan co `to` = NA -> hieu luc den hien tai.
alarm_history_table <- function(stations = NULL, url = NULL, db = NULL) {
  if (is.null(stations)) stations <- load_stations(url = url, db = db)
  rows <- list()
  for (i in seq_len(nrow(stations))) {
    ah <- stations$alarm_history[[i]]
    if (is.null(ah) || !nrow(ah)) next
    for (j in seq_len(nrow(ah))) {
      fr <- ah$from[j]; to <- ah$to[j]
      rows[[length(rows) + 1L]] <- data.frame(
        station = stations$code[i],
        from_day = if (is.na(fr)) NA_character_ else format(fr, "%Y-%m-%d"),
        to_day   = if (is.na(to)) NA_character_ else format(to, "%Y-%m-%d"),
        level_1_m = ah$level_1_m[j], level_2_m = ah$level_2_m[j], level_3_m = ah$level_3_m[j],
        stringsAsFactors = FALSE
      )
    }
  }
  if (!length(rows)) return(NULL)
  do.call(rbind, rows)
}

# Doan `alarm_history` co hieu luc trong ngay `day` (YYYY-MM-DD).
alarm_for_day <- function(alarm_tbl, station, day) {
  if (is.null(alarm_tbl) || !nrow(alarm_tbl)) return(NULL)
  d <- alarm_tbl[alarm_tbl$station == station, , drop = FALSE]
  if (!nrow(d)) return(NULL)
  ok <- rep(TRUE, nrow(d))
  ok <- ok & (is.na(d$from_day) | d$from_day <= day)
  ok <- ok & (is.na(d$to_day)   | d$to_day   >= day)
  d <- d[ok, , drop = FALSE]
  if (!nrow(d)) return(NULL)
  d[1L, , drop = FALSE]
}

# Muc bao dong cao nhat ma dinh dat toi (theo nguong dang hieu luc).
# Tra ve list(level = "", "I", "II", "III"; known = TRUE/FALSE).
exceed_level <- function(peak_m, levels) {
  if (is.null(levels) || !nrow(levels) || is.na(peak_m)) {
    return(list(level = "", known = FALSE))
  }
  l1 <- levels$level_1_m[1]; l2 <- levels$level_2_m[1]; l3 <- levels$level_3_m[1]
  known <- FALSE; best <- ""
  if (!is.na(l3) && peak_m >= l3) return(list(level = "III", known = TRUE))
  if (!is.na(l2) && peak_m >= l2) { best <- "II"; known <- TRUE }
  else if (!is.na(l1) && peak_m >= l1) { best <- "I"; known <- TRUE }
  list(level = best, known = known)
}
