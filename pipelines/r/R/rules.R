# R/rules.R -- Quy tac dung chung cho phan tich R (Chuong 4).
#
# File nay gom:
#  1. Quy tac NGAY CUA DINH (dong bo voi `pipelines/spark/features/rules.py::peak_day_offset`
#     va `pipelines/spark/features/build_training_set.py`).
#  2. Cac ham tai DU LIEU TRIEU (tu `tide_extremes` + `hydromet_bulletins`) dung chung
#     cho `descriptive.R`, `harmonic.R`, `phase_lag.R`, `evaluation.R`.
#  3. Quy tac NGAY QUAN SAT (ngay cua ban tin -> ngay lich cua su kien trieu).
#
# Luat phu thuoc (ADR 0001): khong goi Python, khong import tu `packages/`.
#
# ---------------------------------------------------------------------------
# 1. QUY TAC NGAY CUA DINH
# ---------------------------------------------------------------------------
# `day` cua mot dong = NGAY QUAN SAT cua ban tin KTTV (khong phai ngay lich cua `ts`).
# Mot dinh xuat hien sau nua dem VAN duoc tinh thuoc ngay quan sat truoc do.
# `peakN_day_offset` = 0 neu dinh thuoc ngay quan sat, 1 neu sang ngay ke tiep.
#
# Tham so goc cua `spark/features/rules.py`:
#   DAY_OFFSET_SAME = 0, DAY_OFFSET_NEXT = 1
#   peak_day_offset(obs_day, ts_day): ts_day == obs_day -> 0 ; ts_day > obs_day -> 1 ;
#                                      con lai -> 0
# Ta khai bao LAI day (khong goi Python) va them test tuong duong trong
# `pipelines/r/tests/test_evaluation.R`.

# Gia tri dich ngay -> so ngay lech (theo lich).
day_offset <- function(obs_day, ts_day) {
  if (length(obs_day) == 0L) return(integer(0))
  if (is.null(obs_day) || is.null(ts_day)) return(rep(NA_integer_, length(obs_day)))
  o <- as.character(obs_day); t <- as.character(ts_day)
  out <- rep(0L, length(o))
  out[is.na(o) | is.na(t)] <- NA_integer_
  ok <- !is.na(o) & !is.na(t)
  out[ok & t > o] <- 1L
  out[ok & t < o] <- 0L
  out
}

# ---------------------------------------------------------------------------
# 2. DOC DU LIEU TRIEU (tide_extremes)
# ---------------------------------------------------------------------------
# Cau truc 1 phan tu `tide_extremes`:
#   { ts, meta:{station, kind}, value, qc, qc_flag, raw_value, raw_qc, source_file }
# `ts` luu theo UTC; dung `to_vn()` de quy doi sang gio Viet Nam.
#
# GHI CHU: `mongolite` tra ve cac tai lieu long nhau (`meta`) duoi dang data.frame
# 1-hang; de lay `meta.station` / `meta.kind` on dinh, ta DUNG `aggregate` voi
# `$project` de tach phan thuoc tinh ve bac goc (nhieu tai lieu gop nhung giu nguyen
# ca goc trieu).
#
# Tra ve data.frame cac cot:
#   station, kind, ts (POSIXct gio Viet Nam), value, qc, qc_flag, source_file
load_tide_extremes <- function(qc_ok_only = TRUE, url = NULL, db = NULL) {
  proj <- paste0(
    '{"_id":0,"ts":1,"station":"$meta.station","kind":"$meta.kind","value":1,',
    '"qc":1,"qc_flag":1,"source_file":1}'
  )
  d <- mongo_agg("tide_extremes",
                 paste0('[{"$project":', proj, '}]'),
                 url = url, db = db)
  if (!nrow(d)) stop("Khong doc duoc `tide_extremes`.", call. = FALSE)
  d$ts <- to_vn(d$ts)
  d$station <- as.character(d$station)
  d$kind <- as.character(d$kind)
  d$value <- suppressWarnings(as.numeric(d$value))
  d$qc <- as.character(d$qc)
  d$qc_flag <- as.character(d$qc_flag)
  if (qc_ok_only) {
    keep <- !is.na(d$qc) & d$qc == "ok" &
      (is.na(d$qc_flag) | !(d$qc_flag %in% c("suspect", "rejected")))
    d <- d[keep, , drop = FALSE]
  }
  d$ts_day <- format(d$ts, "%Y-%m-%d")
  d$ts_hour <- as.numeric(format(d$ts, "%H")) +
    as.numeric(format(d$ts, "%M")) / 60 + as.numeric(format(d$ts, "%S")) / 3600
  rownames(d) <- NULL
  d
}

# ---------------------------------------------------------------------------
# 3. NGAY QUAN SAT CUA BAN TIN
# ---------------------------------------------------------------------------
# `hydromet_bulletins.observation_date` = ngay quan sat cua ban tin (chuoi YYYY-MM-DD,
# theo gio Viet Nam). `source_file` la khoa lien ket voi `tide_extremes.source_file`.
# Bang nay la nguon su that cho quy tac "ngay cua dinh = ngay quan sat".
load_bulletin_days <- function(url = NULL, db = NULL) {
  d <- mongo_agg("hydromet_bulletins",
                 paste0('[{"$match":{"source.is_primary":true,',
                        '"bulletin_type":"tide_forecast_5day"}},',
                        '{"$project":{"_id":0,"source_file":1,"observation_date":1,',
                        '"bulletin_date":1,"issued_at":1,"lunar_date.day":1}}]'),
                 url = url, db = db)
  if (!nrow(d)) return(data.frame(source_file = character(0), obs_day = character(0),
                                  lunar_day = numeric(0), stringsAsFactors = FALSE))
  # `observation_date` co the la chuoi "YYYY-MM-DD" hoac "YYYY-MM-DD HH:MM:SS"
  od <- as.character(d$observation_date)
  d$obs_day <- substr(od, 1, 10)
  d$source_file <- as.character(d$source_file)
  ld <- d$lunar_date
  if (is.list(ld)) {
    # mongolite lam phai cac tai lieu long nhau: `lunar_date.day` co the tro thanh
    # mot vector (do mo phang cua data.frame). Lay phan tu dau tien khong rong.
    ld <- vapply(ld, function(z) {
      if (is.null(z)) return(NA_real_)
      if (is.list(z)) {
        if (!is.null(z$day)) return(as.numeric(z$day)[1])
        return(NA_real_)
      }
      v <- suppressWarnings(as.numeric(z))
      if (length(v) == 0L) return(NA_real_)
      v[1]
    }, numeric(1))
  }
  data.frame(source_file = d$source_file, obs_day = d$obs_day,
             lunar_day = as.numeric(ld), stringsAsFactors = FALSE)
}

# ---------------------------------------------------------------------------
# 4. BANG DINH (trạm, ngày quan sát) -> dinh/chan
# ---------------------------------------------------------------------------
# Dung chung cho moi script: gom cac dinh/chan theo (station, obs_day) va sap xep
# theo gio tang dan, gan `peakN_day_offset` bang `day_offset()`.
#
# `day` (ngay quan sat) lay tu `observation_date` cua ban tin; neu tai lieu trieu
# khong co ban tin tuong ung -> dung ngay lich cua `ts` (va day_offset = 0).
build_peak_table <- function(tide, bulletin_days = NULL, obs_day_of = NULL) {
  if (!nrow(tide)) stop("`tide` rong.", call. = FALSE)
  if (is.null(obs_day_of) && !is.null(bulletin_days)) {
    key <- paste0(tide$source_file)
    map <- setNames(bulletin_days$obs_day, bulletin_days$source_file)
    obs_day_of <- unname(map[key])
    # tai lieu khong co ban tin -> ngay lich cua ts
    obs_day_of[is.na(obs_day_of)] <- tide$ts_day[is.na(obs_day_of)]
  }
  if (is.null(obs_day_of)) obs_day_of <- tide$ts_day
  tide$obs_day <- as.character(obs_day_of)
  tide <- tide[order(tide$station, tide$kind, tide$obs_day, tide$ts), , drop = FALSE]
  tide$rn <- ave(seq_len(nrow(tide)),
                  list(tide$station, tide$kind, tide$obs_day), FUN = seq_along)
  tide$day_offset <- day_offset(tide$obs_day, tide$ts_day)
  rownames(tide) <- NULL
  tide
}

# Ham tach 2 dinh + 2 chan theo (station, obs_day) thanh bang rong (de kem noi).
#
# Quy tac: dinh thu N = phan tu thu N theo gio TANG DAN trong ngay quan sat
# (khop voi `build_training_set.py`: `row_number() over (partition by station, obs_day
# order by ts asc)`).
pivot_extremes <- function(tide, prefix = "peak") {
  kind <- if (prefix == "peak") "high" else "low"
  d <- tide[tide$kind == kind, , drop = FALSE]
  d <- d[d$rn <= 2, , drop = FALSE]
  if (!nrow(d)) {
    return(data.frame(station = character(0), day = character(0),
                      h1 = numeric(0), h2 = numeric(0),
                      t1_h = numeric(0), t2_h = numeric(0),
                      off1 = integer(0), off2 = integer(0)))
  }
  # QUY TAC: dinh thu 1 / thu 2 trong NGAY QUAN SAT, sap xep theo gio tang dan
  # (khop voi `build_training_set.py`: `row_number() over (partition by station,
  # obs_day order by ts asc)`).
  #
  # Luu y: PHAI QUAY (pivot) 2 dong (rn=1, rn=2) thanh MOT dong theo
  # (station, day) -- KHONG duoc `!duplicated()` bo dong rn=2 (lam mat dinh thu 2).
  w1 <- d[d$rn == 1, , drop = FALSE]
  w2 <- d[d$rn == 2, , drop = FALSE]
  k1 <- paste(w1$station, w1$obs_day, sep = "\r")
  k2 <- paste(w2$station, w2$obs_day, sep = "\r")
  all_keys <- unique(c(k1, k2))
  idx1 <- match(all_keys, k1)   # chi so trong `w1`
  idx2 <- match(all_keys, k2)   # chi so trong `w2`
  pick_num <- function(v, idx) {
    out <- rep(NA_real_, length(all_keys))
    ok <- !is.na(idx)
    if (any(ok)) out[ok] <- suppressWarnings(as.numeric(v[idx[ok]]))
    out
  }
  pick_chr <- function(v, idx) {
    out <- rep(NA_character_, length(all_keys))
    ok <- !is.na(idx)
    if (any(ok)) out[ok] <- as.character(v[idx[ok]])
    out
  }
  df <- data.frame(
    station = pick_chr(w1$station, idx1),
    day = pick_chr(w1$obs_day, idx1),
    h1 = pick_num(w1$value, idx1), h2 = pick_num(w2$value, idx2),
    t1_h = pick_num(w1$ts_hour, idx1), t2_h = pick_num(w2$ts_hour, idx2),
    off1 = pick_num(w1$day_offset, idx1), off2 = pick_num(w2$day_offset, idx2),
    stringsAsFactors = FALSE)
  df <- df[order(df$station, df$day), , drop = FALSE]
  rownames(df) <- NULL
  df
}

# ---------------------------------------------------------------------------
# 5. MUA / HO / BANG THOI GIAN
# ---------------------------------------------------------------------------
load_rainfall <- function(url = NULL, db = NULL) {
  d <- mongo_agg("rainfall_daily",
                 '[{"$project":{"_id":0,"day":1,"station":1,"rainfall_mm":1,"flag":1}}]',
                 url = url, db = db)
  if (!nrow(d)) return(data.frame())
  d$day <- substr(as.character(d$day), 1, 10)
  d$station <- as.character(d$station)
  d$rainfall_mm <- suppressWarnings(as.numeric(d$rainfall_mm))
  d
}

load_reservoir <- function(url = NULL, db = NULL) {
  d <- mongo_agg("reservoir_daily",
                 paste0('[{"$project":{"_id":0,"day":1,"reservoir":1,"river":1,',
                        '"inflow_m3s":1,"turbine_discharge_m3s":1,',
                        '"spillway_discharge_m3s":1,"upstream_water_level_m":1,',
                        '"rainfall_mm":1}}]'),
                 url = url, db = db)
  if (!nrow(d)) return(data.frame())
  d$day <- substr(as.character(d$day), 1, 10)
  d$reservoir <- as.character(d$reservoir)
  for (nm in c("inflow_m3s", "turbine_discharge_m3s", "spillway_discharge_m3s",
               "upstream_water_level_m", "rainfall_mm")) {
    d[[nm]] <- suppressWarnings(as.numeric(d[[nm]]))
  }
  d
}

load_stations <- function(url = NULL, db = NULL) {
  # `merit` la MOT TAI LIEU LONG NHAU (nested document) -> `$project` tach phan
  # thuoc tinh ve bac goc.
  #
  # LUU Y QUAN TRONG (mongolite 4.x): cac truong co DUONG CHAM trong `$project`
  # (vd "merit.dist_to_root_km") KHONG tra ve cot rieng -- mongolite gop chung
  # vao mot cot data.frame ten "merit" (cac cot con lai cua tai lieu long nhau).
  # Vi vay ta tach thu cong: `merit` -> `merit.dist_to_root_km` (list-column phang).
  d <- mongo_agg("stations",
                 paste0('[{"$project":{"_id":0,"code":1,"name":1,"kind":1,"river":1,',
                        '"location.coordinates":1,',
                        '"merit.COMID":1,"merit.uparea_km2":1,"merit.order":1,',
                        '"merit.dist_to_root_km":1,"merit.basin_root_COMID":1,',
                        '"alarm_history":1,"has_official_forecast":1}}]'),
                 url = url, db = db)
  if (!nrow(d)) return(d)
  d$code <- as.character(d$code)
  # `merit` -> tung cot phang
  if ("merit" %in% names(d)) {
    mk <- d$merit
    if (is.data.frame(mk)) {
      for (nm in names(mk)) d[[paste0("merit.", nm)]] <- mk[[nm]]
    } else {
      # list-column: moi phan tu la 1 tai lieu
      keys <- unique(unlist(lapply(mk, function(z) if (is.null(z)) character(0) else names(z))))
      for (k in keys) {
        d[[paste0("merit.", k)]] <- vapply(mk, function(z) {
          if (is.null(z) || is.null(z[[k]])) return(NA_real_)
          v <- z[[k]]
          if (is.list(v)) v <- unlist(v, use.names = FALSE)
          if (!length(v)) return(NA_real_)
          suppressWarnings(as.numeric(v[1]))
        }, numeric(1))
      }
    }
    d$merit <- NULL
  }
  # `alarm_history` -> LIST-COLUMN: moi phan tu la MOT data.frame (cac doan
  # nguong) hoac rong.
  #
  # LUU Y (mongolite 4.x): khi `$project` chua `alarm_history`, mongolite tra ve
  # cot long nhau voi SO DONG KHAC SO DONG cua tai lieu goc (chi 17/26 tram co
  # nguong) -> khong the danh truc tiep theo vi tri. Vi vay ta DOC RIENG
  # `alarm_history` bang `mongo_find()` (giữ nguyen danh sach theo tai lieu) va
  # ghep theo `code`.
  ah_map <- list()
  ah_raw <- tryCatch(mongo_find("stations",
                                query = '{"alarm_history": {"$exists": true}}',
                                fields = '{"_id":0,"code":1,"alarm_history":1}',
                                url = url, db = db),
                     error = function(e) NULL)
  if (!is.null(ah_raw) && nrow(ah_raw)) {
    for (i in seq_len(nrow(ah_raw))) {
      ah_map[[as.character(ah_raw$code[i])]] <- ah_raw$alarm_history[[i]]
    }
  }
  d$alarm_history <- lapply(as.character(d$code), function(cc) {
    v <- ah_map[[cc]]
    if (is.null(v)) list() else v
  })
  d
}

# ---------------------------------------------------------------------------
# 6. NGUONG BAO DONG THEO THOI GIAN
# ---------------------------------------------------------------------------
# `stations.alarm_history`: moi phan tu co `from`/`to` (chuoi "YYYY-MM-DD HH:MM:SS")
# va 3 nguong `level_1_m`, `level_2_m`, `level_3_m`.
# KHONG dung nguong co dinh: nguong thay doi theo thoi gian (doi so sanh theo NGAY).
#
# Chu hoa `alarm_history` (mot tai lieu long nhau) ve data.frame 6 cot.
# Phan tu co the la: data.frame san, list rong, NULL, hoac list cua nhieu tai lieu.
as_alarm_df <- function(ah) {
  if (is.null(ah)) return(NULL)
  if (is.data.frame(ah)) return(ah)
  if (!is.list(ah) || !length(ah)) return(NULL)
  # list cua nhieu tai lieu long nhau -> noi lai
  parts <- Filter(function(z) !is.null(z) && (is.data.frame(z) || (is.list(z) && length(z))),
                  ah)
  if (!length(parts)) return(NULL)
  parts <- lapply(parts, function(z) {
    if (is.data.frame(z)) return(z)
    df <- as.data.frame(z, stringsAsFactors = FALSE)
    df
  })
  out <- do.call(rbind, lapply(parts, function(p) {
    if (nrow(p) == 1L && ncol(p) == 1L) {
      # 1 tai lieu: mongolite tra ve data.frame 1-hang 1-cot
      p <- as.data.frame(z = p[[1]], stringsAsFactors = FALSE)
    }
    p
  }))
  if (!nrow(out)) return(NULL)
  need <- c("from", "to", "level_1_m", "level_2_m", "level_3_m")
  for (nm in need) if (!nm %in% names(out)) out[[nm]] <- NA
  for (nm in need) out[[nm]] <- suppressWarnings(as.numeric(out[[nm]]))
  out
}

# Tra ve data.frame moi dong: station, day, l1, l2, l3.
alarm_thresholds <- function(stations, days_by_station) {
  rows <- list()
  for (i in seq_len(nrow(stations))) {
    code <- as.character(stations$code[i])
    ah <- stations$alarm_history[[i]]
    d <- days_by_station[[code]]
    if (is.null(d) || !length(d)) next
    # `alarm_history` la MOT TAI LIEU LONG NHAU (nested document): phan tu co the
    # la (a) data.frame 6 cot, (b) list rong, (c) NULL. Chu hoa ve data.frame.
    ah <- as_alarm_df(ah)
    if (is.null(ah) || !nrow(ah)) {
      rows[[length(rows) + 1L]] <- data.frame(station = code, day = d, l1 = NA_real_,
                                               l2 = NA_real_, l3 = NA_real_)
      next
    }
    ah$from <- substr(as.character(ah$from), 1, 10)
    ah$to   <- substr(as.character(ah$to), 1, 10)
    sel <- data.frame(station = code, day = d,
                      l1 = NA_real_, l2 = NA_real_, l3 = NA_real_, stringsAsFactors = FALSE)
    for (k in seq_len(nrow(ah))) {
      f <- ah$from[k]; t <- ah$to[k]
      if (is.na(f) || is.na(t) || !nzchar(f) || !nzchar(t)) next
      m <- sel$day >= f & sel$day <= t
      if (!any(m)) next
      sel$l1[m] <- ah$level_1_m[k]
      sel$l2[m] <- ah$level_2_m[k]
      sel$l3[m] <- ah$level_3_m[k]
    }
    rows[[length(rows) + 1L]] <- sel
  }
  if (!length(rows)) return(data.frame())
  out <- do.call(rbind, rows)
  rownames(out) <- NULL
  out
}

# Muc bao dong cao nhat ma dinh dat: "", "I", "II", "III".
exceed_level_of <- function(peak_m, l1, l2, l3) {
  lv <- rep("", length(peak_m))
  known <- rep(FALSE, length(peak_m))
  ok <- !is.na(peak_m)
  lv[ok & !is.na(l3) & peak_m >= l3] <- "III"
  m2 <- ok & !is.na(l2) & peak_m >= l2 & lv != "III"
  lv[m2] <- "II"
  m1 <- ok & !is.na(l1) & peak_m >= l1 & lv == ""
  lv[m1] <- "I"
  known[ok & (!is.na(l1) | !is.na(l2) | !is.na(l3))] <- TRUE
  list(level = lv, known = known)
}

# ---------------------------------------------------------------------------
# 7. KHOANG CACH DOC THEO CAY MERIT (dung cho H5)
# ---------------------------------------------------------------------------
# `stations.merit.dist_to_root_km` = khoang cach doc (km) tu COMID gan nhat den
# goc luu vuc 44045960. `stations.merit.COMID` = COMID cua nhanh do.
#
# LUU Y: `load_stations()` da tach san `merit.COMID` / `merit.dist_to_root_km`
# ve bac goc (do `$project`), nen o day chi doc truc tiep -- KHONG dung
# `stations$merit[[i]]` (do `merit` da duoc project phang).
station_dist_to_root <- function(stations) {
  code <- as.character(stations$code)
  dd <- rep(NA_real_, length(code))
  cid <- rep(NA_real_, length(code))
  ua <- rep(NA_real_, length(code))
  pick1 <- function(x) {
    if (is.null(x)) return(NA_real_)
    if (is.list(x)) x <- unlist(x, use.names = FALSE)
    if (!length(x)) return(NA_real_)
    suppressWarnings(as.numeric(x[1]))
  }
  for (i in seq_along(code)) {
    dd[i] <- pick1(stations[["merit.dist_to_root_km"]][i])
    cid[i] <- pick1(stations[["merit.COMID"]][i])
    ua[i] <- pick1(stations[["merit.uparea_km2"]][i])
  }
  data.frame(code = code, dist_to_root_km = dd, comid = cid,
             uparea_km2 = ua, stringsAsFactors = FALSE)
}

# ---------------------------------------------------------------------------
# 8. MUA VON BO (theo ngay) -- dung chung
# ---------------------------------------------------------------------------
# Vung mua: moi tram mua gan mot tram muc thuoc da phan loai -> dung mapping
# `stations.merit.COMID` de gan vung. Cho phan tich mo ta ta dung muc bien cua
# luu vuc (comid goc 44045960) cho tat ca cac tram mua.
# Ham tien ich: max/mean bo qua NA, tra NA (khong -Inf) khi tat ca deu NA.
safe_max <- function(x) {
  x <- x[!is.na(x)]
  if (!length(x)) return(NA_real_)
  max(x)
}
safe_mean <- function(x) {
  x <- x[!is.na(x)]
  if (!length(x)) return(NA_real_)
  mean(x)
}

season_of_month <- function(m) {
  # mua mua nam o mien Nam: thang 5-10 la mua mua
  ifelse(m %in% 5:10, "Mùa mưa", "Mùa khô")
}
