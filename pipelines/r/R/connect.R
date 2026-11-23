# R/connect.R -- Ket noi MongoDB (mongolite) va cac hang so dung chung.
#
# Luat phu thuoc (ADR 0001): `pipelines/` khong import tu `packages/`, R khong goi Python.
# File nay chi dung `mongolite` + thu vien chuan cua R.
#
# ---------------------------------------------------------------------------
# QUY TAC CHI QUY DOI THOI GIAN (bat buoc dung chung)
# ---------------------------------------------------------------------------
# Du lieu goc cua KTTV Nam Bo la GIO VIET NAM (UTC+7). MongoDB luu `Date` theo UTC
# (BSON Date luu theo mili-giay ke tu epoch UTC). Voi mongolite 4.x, `POSIXct` ma
# R doc duoc tu BSON Date co `tzone` la "UTC", tuc la moc thoi gian TUYET DOI dung.
#
# => QUY TAC: moi moc thoi gian doc tu MongoDB phai duoc chuyen sang gio Viet Nam
#    bang cach cong them 7 GIO, roi gan `tzone = "Asia/Ho_Chi_Minh"` cho moi
#    moc da quy doi. KHONG dung `as.POSIXct(x, tz = "Asia/Ho_Chi_Minh")` de quy
#    doi (chi doi hien thi, khong doi moc thoi gian tuyet doi).
#
# Ta dung ham `to_vn()` de quy doi MOT LAN duy nhat, va `vn_day()` de lay ngay
# lich Viet Nam (YYYY-MM-DD) cua mot moc thoi gian.
# ---------------------------------------------------------------------------

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1L && is.na(a))) b else a

# Mui gio Viet Nam. R tren Windows co san `Asia/Ho_Chi_Minh` (UCRT, khong can
# tzdata bo sung) -- khai bao o day de moi noi dung chung mot hang so.
PGNN_TZ <- "Asia/Ho_Chi_Minh"

# --- Hang so moi truong (co the ghi de qua bien moi truong) --------------------
r_env <- function() {
  env <- new.env(parent = emptyenv())
  env$repo_root <- Sys.getenv("PGNN_REPO_ROOT", unset = ".")
  env$mongo_url <- Sys.getenv("PGNN_MONGO_URL", unset = "mongodb://localhost:27017")
  env$mongo_db  <- Sys.getenv("PGNN_MONGO_DB",  unset = "vietnam_gis")
  env$rlib      <- Sys.getenv("PGNN_RLIB", unset = file.path(getwd(), ".rlib"))
  env
}

# Ten thu vien R cua repo (`.rlib/`) -- phai dat truoc khi goi `library()`.
use_repo_rlib <- function(root = NULL) {
  root <- root %||% r_env()$repo_root
  lib <- file.path(root, ".rlib")
  if (dir.exists(lib) && !(lib %in% .libPaths())) {
    .libPaths(c(lib, .libPaths()))
  }
  invisible(lib)
}

# ---------------------------------------------------------------------------
# Chuyen doi thoi gian
# ---------------------------------------------------------------------------
# Miet gio Viet Nam (UTC+7) -- dung chung cho MOI xu ly ngay/gio trong GĐ4.
TZ_VN <- "Asia/Ho_Chi_Minh"

# Cong 7 gio len mot moc thoi gian UTC de lay gio Viet Nam (giu moc thoi gian tuyet doi).
#
# GHI CHU QUAN TRONG: sau khi cong 7 gio, moc thoi gian TUYET DOI da la "gio Viet Nam".
# Ta gan `tzone = "Asia/Ho_Chi_Minh"` de viec hien thi (`format`) va viec trich
# "gio trong ngay" cho ra gio Viet Nam. KHONG gan `tzone` (hoac gan "UTC") se lam
# hien thi sai 7 gio.
to_vn <- function(x) {
  if (is.null(x) || length(x) == 0L) return(x)
  if (is.character(x)) {
    x <- as.POSIXct(x, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")
  }
  if (inherits(x, "Date")) {
    return(as.POSIXct(paste(format(x, "%Y-%m-%d"), " 00:00:00"),
                      tz = PGNN_TZ, format = "%Y-%m-%d %H:%M:%S"))
  }
  if (!inherits(x, "POSIXct")) {
    x <- as.POSIXct(x, tz = "UTC", format = "%Y-%m-%d %H:%M:%S")
  }
  # `x` la moc thoi gian TUYET DOI (epoch, gio UTC). Gio Viet Nam = moc do + 7 gio.
  #
  # CACH DUNG: cong 7 gio vao moc thoi gian UTC (giu dang epoch), roi DICH
  # chuoi "YYYY-MM-DD HH:MM:SS" THEO gio Viet Nam va phuc toi POSIXct voi
  # `tz = PGNN_TZ`. Khi do gia tri epoch moi tro lai dung moc thoi gian UTC
  # tuyet doi, va hien thi ra gio Viet Nam.
  #
  # LUU Y: KHONG duoc lam `x + 7*3600` roi gan `tzone = "Asia/Ho_Chi_Minh"`:
  # R hieu gia tri numeric la moc thoi gian UTC va DICH them 7 gio mot lan nua
  # khi hien thi -> sai 7 gio (doi chieu hai lan).
  x7 <- x + 7 * 3600
  s <- format(x7, "%Y-%m-%d %H:%M:%S", tz = "UTC")
  as.POSIXct(s, tz = PGNN_TZ, format = "%Y-%m-%d %H:%M:%S")
}

# Ngay lich Viet Nam (YYYY-MM-DD) cua mot moc thoi gian.
vn_day <- function(x) format(to_vn(x), "%Y-%m-%d", tz = PGNN_TZ)

# Gio trong ngay (0..24, phan thuc) cua mot moc thoi gian, theo gio Viet Nam.
# `vn_hour()` dung chung cho moi noi trich "gio dinh/chan" (khop voi
# `pipelines/spark/features/build_training_set.py::_hour_of_day`).
vn_hour <- function(x) {
  if (is.null(x) || length(x) == 0L) return(x)
  if (is.character(x)) {
    x <- as.POSIXct(x, tz = TZ_VN, format = "%Y-%m-%d %H:%M:%S")
  }
  t <- to_vn(x)
  as.numeric(format(t, "%H", tz = TZ_VN)) +
    as.numeric(format(t, "%M", tz = TZ_VN)) / 60 +
    as.numeric(format(t, "%S", tz = TZ_VN)) / 3600
}

# Moc 00:00 gio Viet Nam cua mot chuoi ngay "YYYY-MM-DD".
vn_midnight <- function(day) {
  as.POSIXct(paste0(as.character(day), " 00:00:00"), tz = TZ_VN,
             format = "%Y-%m-%d %H:%M:%S")
}

# Chuyen nguoc: gio Viet Nam -> UTC (la ham nghich cua `to_vn`).
vn_to_utc <- function(x) {
  if (is.null(x) || length(x) == 0L) return(x)
  if (inherits(x, "POSIXct")) {
    # trich chuoi gio Viet Nam, tru 7 gio roi phuc toi epoch UTC
    s <- format(x, "%Y-%m-%d %H:%M:%S", tz = PGNN_TZ)
    as.POSIXct(s, tz = "UTC", format = "%Y-%m-%d %H:%M:%S") - 7 * 3600
  } else {
    as.POSIXct(x, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")
  }
}

# ---------------------------------------------------------------------------
# Ket noi MongoDB
# ---------------------------------------------------------------------------
# LUU Y QUAN TRONG (mongolite 4.x): thu tu tham so cua `mongolite::mongo()` la
#   mongo(collection, db, url, ...)
# KHONG phai (url, db, collection). Neu goi sai thu tu, R bao
# "Invalid database name" / "Invalid JSON object". Vi vay ham nay luon goi
# bang TEN tham so.
mongo_connect <- function(url = NULL, db = NULL, collection = "stations") {
  use_repo_rlib()
  if (!requireNamespace("mongolite", quietly = TRUE)) {
    stop("Thieu goi 'mongolite'. Cai: install.packages('mongolite', lib='.rlib')", call. = FALSE)
  }
  mongolite::mongo(collection = collection,
                   db = db %||% r_env()$mongo_db,
                   url = url %||% r_env()$mongo_url)
}

# Doc mot collection thanh data.frame.
mongo_find <- function(collection, query = "{}", fields = "{}", sort = "{}", limit = 0, skip = 0,
                       url = NULL, db = NULL) {
  m <- mongo_connect(url = url, db = db, collection = collection)
  on.exit(m$disconnect(), add = TRUE)
  m$find(query = query, fields = fields, sort = sort, skip = skip, limit = limit)
}

mongo_count <- function(collection, query = "{}", url = NULL, db = NULL) {
  m <- mongo_connect(url = url, db = db, collection = collection)
  on.exit(m$disconnect(), add = TRUE)
  m$count(query = query)
}

# Chay mot pipeline aggregation; ket qua la data.frame (nested -> list/data.frame).
# `pipeline` co the la:
#   - mot chuoi JSON duy nhat:  '[{"$match":...},{"$project":...}]'
#   - mot vector chuoi JSON:   c('[{"$match":...}]', '[{"$project":...}]')  -> noi lai
mongo_agg <- function(collection, pipeline, url = NULL, db = NULL) {
  m <- mongo_connect(url = url, db = db, collection = collection)
  on.exit(m$disconnect(), add = TRUE)
  if (length(pipeline) > 1L) {
    pipeline <- paste0("[", paste0(pipeline, collapse = ","), "]")
  }
  m$aggregate(pipeline)
}

# ---------------------------------------------------------------------------
# QUY TAC NGAY CUA DINH (dung chung voi `pipelines/spark/features/rules.py`)
# ---------------------------------------------------------------------------
# `day` cua mot dong = NGAY QUAN SAT cua ban tin (khong phai ngay lich cua `ts`).
# `peakN_day_offset` = 0 neu dinh thuoc ngay quan sat, 1 neu sang ngay ke tiep.
DAY_OFFSET_SAME <- 0L
DAY_OFFSET_NEXT <- 1L

peak_day_offset <- function(obs_day, ts_day) {
  if (is.null(obs_day) || is.null(ts_day) || is.na(obs_day) || is.na(ts_day)) {
    return(NA_integer_)
  }
  if (ts_day == obs_day) return(DAY_OFFSET_SAME)
  if (ts_day > obs_day)  return(DAY_OFFSET_NEXT)
  DAY_OFFSET_SAME
}

# ---------------------------------------------------------------------------
# Tien ich
# ---------------------------------------------------------------------------
day_span <- function(from, to) as.integer(as.Date(to) - as.Date(from))

# Chia n quan sat vao k nhom ngau nhien (bootstrap theo ngay).
make_folds <- function(n, k, seed = 20261123) {
  set.seed(seed)
  idx <- sample.int(n, n, replace = FALSE)
  cut <- c(0, floor(seq_len(k) * n / k), n)
  lapply(seq_len(k), function(i) idx[(cut[i] + 1L):cut[i + 1L]])
}

say <- function(...) {
  cat(sprintf("[%s] %s\n", format(Sys.time(), "%H:%M:%S"), paste0(...)), file = stderr())
  invisible(NULL)
}

say2 <- function(...) {
  cat(paste0(...), "\n")
  invisible(NULL)
}
