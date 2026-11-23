# R/evaluation.R -- Ham danh gia CHUNG cho MOI mo hinh (Chuong 4, muc 4.5).
#
#   Rscript pipelines/r/evaluation.R
#
# HAM DANH GIA DUNG DUOC CHO: B0, B_KTTV, B_harm, B1..B5, PhaseGNN -- bat ky mo hinh
# nao xuat ra ban ghi theo `schemas/prediction/v1.schema.json`.
#
# Dinh dang ban ghi (xem `schemas/prediction/v1.schema.json`):
#   model, station, issued_date, target_date, lead_day, peak_idx,
#   h_q10, h_q50, h_q90, t_peak
#   - `peak_idx`: 0 = dinh thu nhat trong ngay, 1 = dinh thu hai.
#   - `t_peak`: ISO 8601 co UTC (vd '2024-03-10T02:00:00Z').
#
# Luat phu thuoc (ADR 0001): khong goi Python, khong import tu `packages/`.
#
# ---------------------------------------------------------------------------
# QUY TAC GHEP DINH DU BAO - THUC DO (±6 GIO)
# ---------------------------------------------------------------------------
# Mot ban ghi du bao cho (model, station, target_date, peak_idx) co:
#   - do cao du bao: `h_q50` (hoac nhieu muc phan vi: dung muc trung vi cho
#     chi so don, va bao cao them do phu cho moc muc 10%/90%);
#   - thoi diem du bao: `t_peak` (ISO 8601 UTC -> quy doi sang gio Viet Nam).
#
# GHEP: voi moi (station, target_date), ta lay TAT CA dinh thuc do cua ngay do
# (theo quy tac ngay cua ban tin: `day` = ngay quan sat, dinh thu nhat/thu hai
# theo gio tang dan) va moi dinh du bao (peak_idx 0/1), roi khop mot dinh du bao
# voi MOT dinh thuc do sao cho |t_du_bao - t_thuc_do| <= 6 gio, va CHON cap
# (du_bao, thuc_do) co |t_du_bao - t_thuc_do| NHO NHAT (khop 1-1).
#
# - `peak_idx` duoc dung de UU TIEN khop dinh cung chi so (0<->0, 1<->1) truoc,
#   sau do moi dung khop theo thoi gian cho cac cap con lai. Cach nay tranh
#   tinh huong dinh 1 du bao bi khop nham voi dinh 2 thuc do (nguon sai so lon).
# - Loc `|dt| <= 6 gio`: dac biet QUAN TRONG cho viec tinh MAE gio (dt) -- neu
#   khong loc, mot du bao sai ngay se ra sai so ~24 gio.
#
# Cac chi so:
#   MAE, RMSE          : sai so do cao (m) tren cac cap da ghep
#   bias               : sai co he (do du bao - thuc do) (m)
#   DeltaH (MAE, bias) : sai so do cao nhanh voi ky vong (cung MAE/bias nhung
#                         bao cao rieng theo nhom lead_day / peak_idx)
#   DeltaT (MAE, bias) : sai so gio dinh (h) va do lech (h)
#   NSE, KGE           : Nash-Sutcliffe Efficiency va Kling-Gupta Efficiency
#                         (dung `hydroGOF` da co san trong `.rlib`)
#   POD, FAR, CSI      : chi so du bao bao dong (hits / false alarms / critical)
#   skill so voi KTTV  : so sanh voi du bao chinh thuc trong `official_forecasts`
#   Kiem dinh          : Wilcoxon (theo cap), Diebold-Mariano, Holm

# ---------------------------------------------------------------------------
# 1. DOC BAN GHI DU BAO
# ---------------------------------------------------------------------------
# Doc file du bao (CSV hoac Parquet). Co the chua NHIEU model trong cung file ->
# tach theo cot `model`.
read_predictions <- function(path) {
  ext <- tolower(tools::file_ext(path))
  df <- if (ext == "parquet") {
    read_parquet_df(path)
  } else {
    read_csv_df(path)
  }
  # chuan hoa ten cot (de khop voi schema)
  nms <- names(df)
  ren <- c(model = "model", station = "station", issued_date = "issued_date",
           target_date = "target_date", lead_day = "lead_day", peak_idx = "peak_idx",
           h_q10 = "h_q10", h_q50 = "h_q50", h_q90 = "h_q90", t_peak = "t_peak")
  for (k in names(ren)) {
    if (!k %in% nms) {
      alt <- nms[grepl(paste0("^", k, "$", collapse = ""), nms, ignore.case = TRUE)]
      if (length(alt)) names(df)[nms == alt[1]] <- k
    }
  }
  df$model <- as.character(df$model)
  df$station <- as.character(df$station)
  df$issued_date <- substr(as.character(df$issued_date), 1, 10)
  df$target_date <- substr(as.character(df$target_date), 1, 10)
  df$lead_day <- suppressWarnings(as.integer(df$lead_day))
  df$peak_idx <- suppressWarnings(as.integer(df$peak_idx))
  for (k in c("h_q10", "h_q50", "h_q90")) {
    df[[k]] <- suppressWarnings(as.numeric(df[[k]]))
  }
  df$t_peak_vn <- parse_t_peak_utc(df$t_peak)
  df
}

# `t_peak` theo ISO 8601 co UTC: '2024-03-10T02:00:00Z'.
# Quy doi sang gio Viet Nam (cong 7 gio) va tach ra gio trong ngay (0..24).
#
# LUU Y: `readr::read_csv()` TU DONG nhan dien cot ISO-8601 co 'Z' va chuyen
# thanh POSIXct (UTC) -> chuoi thanh "2024-03-10 12:30:00 UTC". Vi vay ham
# nay phai xu ly CA HAI dang: doi tuong POSIXct va chuoi ISO/chuoi co khoang
# trang "UTC".
parse_t_peak_utc <- function(x) {
  if (is.null(x)) return(as.POSIXct(character(0)))
  # (1) da la POSIXct/POSIXlt -> quy doi truc tiep
  if (inherits(x, "POSIXt")) {
    t_utc <- as.POSIXct(x, tz = "UTC")
    return(to_vn(t_utc))
  }
  s <- as.character(x)
  s[is.na(s)] <- ""
  # chuan hoa: bo khoang trang, doi ' ' thanh 'T', them 'Z' neu chua co
  s <- gsub("\\s+", "T", trimws(s))
  s <- sub("T?UTC$", "Z", s)
  s <- sub("Z?$", "", s)
  has_z <- grepl("Z$", s)
  s <- sub("Z$", "", s)
  s <- paste0(s, "Z")
  s[!nzchar(s)] <- NA_character_
  t0 <- as.POSIXct(s, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  to_vn(t0)
}

# ---------------------------------------------------------------------------
# 2. DOC DU BAO CHINH THUC (KTTV) -- baseline
# ---------------------------------------------------------------------------
# `official_forecasts`: {station, issued_date, target_date, lead_day,
#   high: [{ts, value, qc}], low: [{ts, value, qc}]}
# Chuyen thanh ban ghi cung dinh nhieu giong ban ghi du bao (peak_idx 0/1).
load_official_forecasts <- function(url = NULL, db = NULL) {
  # GHI CHU HIEU NANG: dung `$unwind` trong MongoDB de tach mang `high` thanh
  # cac dong ngang hang. Cach nay nhanh hon vong lap R (38.410 ban ghi -> 76.820
  # dong du bao) va tranh phai goi `as.data.frame()` 38.410 lan.
  d <- mongo_agg("official_forecasts",
                 paste0('[{"$unwind":"$high"},',
                        '{"$project":{"_id":0,"station":1,"issued_date":1,',
                        '"target_date":1,"lead_day":1,"ts":"$high.ts",',
                        '"value":"$high.value"}},',
                       '{"$sort":{"station":1,"issued_date":1,"target_date":1,"ts":1}}]'),
                 url = url, db = db)
  if (!nrow(d)) return(NULL)
  out <- data.frame(
    model = "B_KTTV", station = as.character(d$station),
    issued_date = substr(as.character(d$issued_date), 1, 10),
    target_date = substr(as.character(d$target_date), 1, 10),
    lead_day = suppressWarnings(as.integer(d$lead_day)),
    h_q10 = NA_real_, h_q50 = suppressWarnings(as.numeric(d$value)),
    h_q90 = NA_real_,
    t_peak = as.character(d$ts),
    stringsAsFactors = FALSE)
  out <- out[!is.na(out$h_q50) & !is.na(out$t_peak) & nzchar(out$t_peak), , drop = FALSE]
  if (!nrow(out)) return(NULL)
  # `t_peak` co the da la POSIXct (BSON Date) hoac chuoi ISO -> chuan hoa
  out$t_peak_vn <- parse_t_peak_utc(out$t_peak)
  # peak_idx = thu tu dinh trong ngay (theo gio tang dan)
  out$peak_idx <- ave(seq_len(nrow(out)),
                      list(out$station, out$issued_date, out$target_date),
                      FUN = seq_along) - 1L
  # chuan hoa `t_peak` thanh chuoi ISO UTC
  out$t_peak <- format(out$t_peak_vn - 7 * 3600, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  rownames(out) <- NULL
  out
}

# ---------------------------------------------------------------------------
# 3. BANG THUC DO (dinhh/cham theo ngay quan sat)
# ---------------------------------------------------------------------------
# Dung `rules.R::build_peak_table()` + `pivot_extremes()` de lay:
#   station, day (= ngay quan sat), h1, h2, t1_h, t2_h
observed_peak_table <- function(url = NULL, db = NULL) {
  tide <- load_tide_extremes(qc_ok_only = TRUE, url = url, db = db)
  bul <- load_bulletin_days(url = url, db = db)
  pt <- build_peak_table(tide, bul)
  pk <- pivot_extremes(pt, "peak")
  lo <- pivot_extremes(pt, "low")
  names(pk)[names(pk) %in% c("h1", "h2", "t1_h", "t2_h")] <-
    c("obs_h1", "obs_h2", "obs_t1_h", "obs_t2_h")
  names(lo)[names(lo) %in% c("h1", "h2", "t1_h", "t2_h")] <-
    c("obs_l1", "obs_l2", "obs_lt1_h", "obs_lt2_h")
  out <- merge(pk, lo, by = c("station", "day"), all = TRUE)
  out$year <- as.integer(substr(out$day, 1, 4))
  out
}

# ---------------------------------------------------------------------------
# 4. GHEP DU BAO - THUC DO (±6 GIO)
# ---------------------------------------------------------------------------
# `match_forecast_observed(pred, obs, window_h = 6)`:
#   - `pred`: bang du bao (chu `model, station, target_date, lead_day, peak_idx,
#     h_q50, t_peak_vn`)
#   - `obs`: bang thuc do (chu `station, day, obs_h1, obs_h2, obs_t1_h, obs_t2_h`)
#
# Tra ve bang ghep voi cac cot:
#   station, target_date, model, lead_day, peak_idx, h_q50, t_peak_vn,
#   obs_h, obs_t_h, d_h (do cao - thuc do), d_t_h (gio du bao - gio thuc do),
#   matched_by ("peak_idx" hoac "time")
match_forecast_observed <- function(pred, obs, window_h = 6) {
  if (!nrow(pred) || !nrow(obs)) {
    return(data.frame(station = character(0), target_date = character(0),
                      model = character(0), lead_day = integer(0),
                      peak_idx = integer(0), h_q50 = numeric(0),
                      t_peak_vn = as.POSIXct(character(0)),
                      obs_h = numeric(0), obs_t_h = numeric(0),
                      d_h = numeric(0), d_t_h = numeric(0),
                      matched_by = character(0)))
  }
  # tach bang thuc do thanh 2 dong moi dinh (peak_idx 0/1)
  ol <- list()
  for (k in 0:1) {
    ol[[k + 1]] <- data.frame(
      station = obs$station, target_date = obs$day,
      obs_h = if (k == 0) obs$obs_h1 else obs$obs_h2,
      obs_t_h = if (k == 0) obs$obs_t1_h else obs$obs_t2_h,
      obs_peak_idx = k, stringsAsFactors = FALSE)
  }
  ol <- do.call(rbind, ol)
  ol <- ol[!is.na(ol$obs_h) & !is.na(ol$obs_t_h), , drop = FALSE]

  # khop khoa (station, target_date)
  key_p <- paste(pred$station, pred$target_date, sep = "|")
  key_o <- paste(ol$station, ol$target_date, sep = "|")
  idx_o <- split(seq_len(nrow(ol)), key_o)

  # --- VECTOR HOA (thay cho vong lap tao data.frame tung dong) ---------------
  # 1. moc 00:00 cua `target_date` cho MOI ban ghi du bao -> vector
  day0 <- as.POSIXct(paste0(pred$target_date, " 00:00:00"), tz = PGNN_TZ,
                      format = "%Y-%m-%d %H:%M:%S")
  hp_all <- as.numeric(difftime(pred$t_peak_vn, day0, units = "hours"))
  hp_mod <- hp_all %% 24

  # ---------------------------------------------------------------------------
  # 1b. DU BAO KHONG CO GIO DINH (`t_peak` rong) -> GHEP THEO NGAY.
  #
  # Vi du: du bao climatology (B0) chi co do cao, khong co thoi diem dinh. Voi
  # nhung du bao nay ta khop theo (station, target_date, peak_idx) truc tiep --
  # KHONG ap dung loc ±6 gio (khong co moc thoi gian de so sanh) va KHONG tinh
  # chi so gio (DeltaT) cho cac cap nay.
  # ---------------------------------------------------------------------------
  no_time <- !is.finite(hp_all)
  res_extra <- NULL
  if (any(no_time)) {
    sub <- pred[no_time, , drop = FALSE]
    ol_sub <- ol
    # khop theo (station, target_date, peak_idx)
    kk <- paste(sub$station, sub$target_date, sub$peak_idx, sep = "|")
    ko <- paste(ol_sub$station, ol_sub$target_date, ol_sub$obs_peak_idx, sep = "|")
    j <- match(kk, ko)
    ok <- !is.na(j)
    if (any(ok)) {
      sub <- sub[ok, , drop = FALSE]; j <- j[ok]
      res_extra <- data.frame(
        station = sub$station, target_date = sub$target_date,
        model = sub$model, lead_day = sub$lead_day,
        peak_idx = sub$peak_idx, h_q50 = sub$h_q50,
        t_peak_vn = sub$t_peak_vn,
        obs_h = ol_sub$obs_h[j], obs_t_h = ol_sub$obs_t_h[j],
        d_h = sub$h_q50 - ol_sub$obs_h[j],
        d_t_h = NA_real_,
        matched_by = "day", stringsAsFactors = FALSE)
    }
  }

  # 2. mo rong chi muc "tram|ngay" -> danh sach chi so cac dong thuc do
  #    (moi ngay co toi da 4 dong: 2 dinh x 2 moc)
  #
  # VECTOR HOA: dung `split` cho CA HAI phia (du bao va thuc do) de tranh
  # quet toan bo bang trong moi vong lap (O(n^2) -> O(n)).
  idx_p <- split(seq_len(nrow(ol)), key_o)
  idx_p <- lapply(idx_p, as.integer)
  idx_pred <- split(seq_len(nrow(pred)), key_p)
  idx_pred <- lapply(idx_pred, as.integer)

  keep_idx <- integer(0)
  j_obs <- integer(0)
  by_vec <- character(0)
  for (k in intersect(names(idx_pred), names(idx_p))) {
    cand <- idx_p[[k]]
    pi_ <- idx_pred[[k]]
    if (is.null(cand) || !length(cand)) next
    if (!length(pi_)) next
    pi_ <- pi_[is.finite(hp_all[pi_])]
    if (!length(pi_)) next
    # Ma tran "khoang cach gio vong 24" giua cac moc du bao (hang) va cac
    # moc thuc do (cot):
    #   dd = |hp - obs_t| ; dd_c = min(dd %% 24, 24 - (dd %% 24))
    # `outer` luon tra ve ma tran (ke ca khi 1 x 1) -> khong loi "dimensions".
    dt_all <- abs(outer(hp_mod[pi_], ol$obs_t_h[cand], function(a, b) a - b))
    dt_all <- dt_all %% 24
    dt_all <- pmin(dt_all, 24 - dt_all)
    for (ii in seq_along(pi_)) {
      i <- pi_[ii]
      dt <- dt_all[ii, ]
      same <- cand[ol$obs_peak_idx[cand] == pred$peak_idx[i]]
      if (length(same)) {
        j <- same[which.min(dt[match(same, cand)])]
        by <- "peak_idx"
      } else {
        j <- cand[which.min(dt)]
        by <- "time"
      }
      dtj <- dt[match(j, cand)]
      if (!is.finite(dtj) || dtj > window_h) next
      keep_idx <- c(keep_idx, i)
      j_obs <- c(j_obs, j)
      by_vec <- c(by_vec, by)
    }
  }
  if (!length(keep_idx)) {
    empty <- data.frame(station = character(0), target_date = character(0),
                      model = character(0), lead_day = integer(0),
                      peak_idx = integer(0), h_q50 = numeric(0),
                      t_peak_vn = as.POSIXct(character(0)),
                      obs_h = numeric(0), obs_t_h = numeric(0),
                      d_h = numeric(0), d_t_h = numeric(0),
                      matched_by = character(0))
    if (!is.null(res_extra) && nrow(res_extra)) {
      res_extra$t_peak_vn <- as.POSIXct(res_extra$t_peak_vn)
      return(res_extra)
    }
    return(empty)
  }
  hp_sel <- hp_all[keep_idx]
  hp_day <- hp_mod[keep_idx]
  # SAI SO GIO (DeltaT): phai so sanh hai MOC TRONG NGAY (0..24) -> dung
  # `hp_day` (gio modulo 24), KHONG dung `hp_sel` (gio tuyet doi co the > 24).
  # Sai so theo vong 24: dt = (hp - obs) %% 24, chuan hoa ve [-12, 12).
  dt_signed <- (hp_day - ol$obs_t_h[j_obs]) %% 24
  dt_signed[dt_signed > 12] <- dt_signed[dt_signed > 12] - 24
  res <- data.frame(
    station = pred$station[keep_idx], target_date = pred$target_date[keep_idx],
    model = pred$model[keep_idx], lead_day = pred$lead_day[keep_idx],
    peak_idx = pred$peak_idx[keep_idx], h_q50 = pred$h_q50[keep_idx],
    t_peak_vn = pred$t_peak_vn[keep_idx],
    obs_h = ol$obs_h[j_obs], obs_t_h = ol$obs_t_h[j_obs],
    d_h = pred$h_q50[keep_idx] - ol$obs_h[j_obs],
    d_t_h = dt_signed,
    matched_by = by_vec, stringsAsFactors = FALSE)
  rownames(res) <- NULL
  if (!is.null(res_extra) && nrow(res_extra)) {
    res <- rbind(res, res_extra)
    res <- res[order(res$model, res$station, res$target_date, res$peak_idx), ,
               drop = FALSE]
    rownames(res) <- NULL
  }
  res
}

# ---------------------------------------------------------------------------
# 5. CHI SO
# ---------------------------------------------------------------------------
# Chi so don le cho mot tap ghep.
compute_metrics <- function(matched, obs_col = "obs_h", fcst_col = "h_q50") {
  if (!nrow(matched)) return(NULL)
  o <- matched[[obs_col]]; p <- matched[[fcst_col]]
  ok <- !is.na(o) & !is.na(p)
  o <- o[ok]; p <- p[ok]
  if (!length(o)) return(NULL)
  e <- p - o
  out <- data.frame(
    n = length(o),
    mae = mean(abs(e)),
    rmse = sqrt(mean(e^2)),
    bias = mean(e),
    mae_t_h = NA_real_, bias_t_h = NA_real_, n_time = NA_integer_,
    nse = NA_real_, kge = NA_real_,
    stringsAsFactors = FALSE)
  # sai so gio
  if (!is.null(matched$d_t_h)) {
    dt <- matched$d_t_h[ok]
    dt <- dt[!is.na(dt)]
    if (length(dt)) {
      out$mae_t_h <- mean(abs(dt))
      out$bias_t_h <- mean(dt)
      out$n_time <- length(dt)
    }
  }
  # NSE / KGE
  if (length(o) > 1) {
    if (requireNamespace("hydroGOF", quietly = TRUE)) {
      out$nse <- hydroGOF::NSE(sim = p, obs = o, na.rm = TRUE)
      out$kge <- hydroGOF::KGE(sim = p, obs = o, na.rm = TRUE)
    }
  }
  out
}

# Chi so bao dong (POD / FAR / CSI) tren nguong bao dong.
# `events`: bo to hop cho MOI (station, target_date) -- co bao dong hay khong.
# `exceed_level` la muc ("", "I", "II", "III") cua ngay do.
contingency_scores <- function(matched, exceed_by_day, thresholds = NULL,
                               threshold = "I") {
  if (!nrow(matched)) return(NULL)
  key <- paste(matched$station, matched$target_date, sep = "|")
  lv <- exceed_by_day
  uk <- unique(key)
  if (is.null(thresholds) || !length(thresholds)) {
    return(data.frame(threshold = character(0), n = integer(0), hits = integer(0),
                      misses = integer(0), false_alarms = integer(0),
                      negatives = integer(0), POD = numeric(0), FAR = numeric(0),
                      CSI = numeric(0), stringsAsFactors = FALSE))
  }
  # muc bao dong cua ngay -> dung nguong TUONG UNG de quy doi du bao
  ord <- c("", "I", "II", "III")
  rows <- list()
  # VECTOR HOA: gom theo khoa "tram|ngay" mot lan thay vi quet `key == k`
  # trong vong lap (O(n^2)).
  m_idx <- split(seq_len(nrow(matched)), key)
  # tra nguong theo khoa: dung `match` vao vector (O(n log n)) thay vi
  # tra cuou list co ten (O(n) moi lan).
  #
  # `thresholds` co the la list CO TEN (khoa "tram|ngay") hoac list khong ten.
  # Chu hoa ve list co ten theo `uk` de moi thu tu deu dung.
  tk <- names(thresholds)
  if (is.null(tk) || any(!nzchar(tk))) {
    tk <- uk
    names(thresholds) <- uk
  }
  thr_I <- rep(NA_real_, length(tk)); thr_II <- thr_I; thr_III <- thr_I
  for (i in seq_along(tk)) {
    e <- thresholds[[i]]
    if (is.null(e)) next
    thr_I[i] <- suppressWarnings(as.numeric(e[["I"]]))
    thr_II[i] <- suppressWarnings(as.numeric(e[["II"]]))
    thr_III[i] <- suppressWarnings(as.numeric(e[["III"]]))
  }
  # muc bao dong thuc te: `lv` la named character vector -> dung `match`
  # VECTOR HOA: tra `lv` cho TAT CA khoa `uk` MOT LAN (O(n log n)) thay vi tra
  # cuou vector co ten trong vong lap (O(n) moi lan -> O(n^2)).
  lv_all <- lv[match(uk, names(lv))]
  for (th in c("I", "II", "III")) {
    kk <- intersect(uk, names(thresholds))
    if (!length(kk)) next
    thr_v <- switch(th, I = thr_I, II = thr_II, III = thr_III)
    thr_v <- thr_v[match(kk, tk)]
    lv_k <- lv_all[match(kk, uk)]
    hits <- 0; misses <- 0; fa <- 0; neg <- 0
    for (ki in seq_along(kk)) {
      k <- kk[ki]
      m <- m_idx[[k]]
      if (is.null(m)) next
      thr <- thr_v[ki]
      if (is.na(thr)) next
      lvk <- lv_k[ki]
      # `""` = ngay KHONG co bao dong -> van phai tinh vao nhom "negatives".
      # Chi bo qua khi muc bao dong KHONG XAC DINH (NA).
      if (is.na(lvk)) next
      # ngay duoc bao dong khi muc THUC TE >= muc nguong
      obs_hit <- match(lvk, ord) >= match(th, ord)
      # du bao duoc bao dong khi dinh du bao >= nguong cua muc `th`
      p_max <- max(matched$h_q50[m], na.rm = TRUE)
      if (!is.finite(p_max)) next
      fc_hit <- p_max >= thr
      if (obs_hit && fc_hit) hits <- hits + 1
      else if (obs_hit && !fc_hit) misses <- misses + 1
      else if (!obs_hit && fc_hit) fa <- fa + 1
      else neg <- neg + 1
    }
    pod <- if ((hits + misses) > 0) hits / (hits + misses) else NA_real_
    far <- if ((hits + fa) > 0) fa / (hits + fa) else NA_real_
    csi <- if ((hits + misses + fa) > 0) hits / (hits + misses + fa) else NA_real_
    rows[[length(rows) + 1L]] <- data.frame(
      threshold = th, n = hits + misses + fa + neg, hits = hits, misses = misses,
      false_alarms = fa, negatives = neg, POD = pod, FAR = far, CSI = csi,
      stringsAsFactors = FALSE)
  }
  if (!length(rows)) return(NULL)
  do.call(rbind, rows)
}

# ---------------------------------------------------------------------------
# 6. KIEM DINH THONG KE
# ---------------------------------------------------------------------------
# Wilcoxon (theo cap) giua hai mo hinh tren cac cap da ghep CHUNG.
wilcoxon_paired <- function(m1, m2) {
  a <- m1$d_h; b <- m2$d_h
  k <- paste(m1$station, m1$target_date, m1$peak_idx, sep = "|")
  k2 <- paste(m2$station, m2$target_date, m2$peak_idx, sep = "|")
  cm <- intersect(k, k2)
  if (length(cm) < 5) return(NULL)
  a <- a[match(cm, k)]; b <- b[match(cm, k2)]
  # R's wilcox.test gives NA p-value when ALL differences are 0 (W = 0).
  # Theo quy uoc, truong hop do = "khong co bang chung khac biet" -> p = 1.
  d <- a - b
  if (all(abs(d) < .Machine$double.eps)) {
    return(data.frame(n_pairs = length(cm), stat_w = 0, p_value = 1,
                      median_a = stats::median(a), median_b = stats::median(b),
                      stringsAsFactors = FALSE))
  }
  w <- stats::wilcox.test(a, b, paired = TRUE, exact = FALSE)
  p <- w$p.value
  if (is.na(p)) p <- 1
  data.frame(n_pairs = length(cm),
             stat_w = unname(w$statistic), p_value = p,
             median_a = stats::median(a), median_b = stats::median(b),
             stringsAsFactors = FALSE)
}

# Diebold-Mariano: kiem dinh mean(d) = 0 cho hieu sai so `d`.
# Ban loss: MAE (hoac RMSE) voi dau vao MAE. Uoc luong lon bang HAC (Newey-West,
# lag = floor(4*(n/100)^(2/9))).
dm_test <- function(d, loss = c("abs", "sq"), lag = NULL) {
  loss <- match.arg(loss)
  d <- d[!is.na(d)]
  n <- length(d)
  if (n < 10) return(NULL)
  l <- if (loss == "abs") abs(d) else d^2
  m <- mean(l)
  u <- l - m
  if (is.null(lag)) lag <- floor(4 * (n / 100)^(2 / 9))
  lag <- as.integer(min(lag, n - 1))
  # HAC (Newey-West) variance cua trung binh
  g0 <- sum(u^2) / n
  s <- 0
  for (k in seq_len(lag)) {
    gk <- sum(u[(k + 1):n] * u[1:(n - k)]) / n
    s <- s + 2 * (1 - k / (lag + 1)) * gk
  }
  v <- (g0 + s) / n
  # Trường hợp sai số hằng số (u = 0): phương sai = 0 -> không có bằng chứng
  # khác biệt -> p = 1 (thay vì NULL).
  if (!is.finite(v)) return(NULL)
  if (v <= 0) {
    return(data.frame(n = n, loss = loss, mean_loss = m, dm_t = NA_real_,
                      p_value = 1, stringsAsFactors = FALSE))
  }
  t <- m / sqrt(v)
  p <- 2 * stats::pnorm(abs(t), lower.tail = FALSE)
  data.frame(n = n, loss = loss, mean_loss = m, dm_t = t, p_value = p,
             stringsAsFactors = FALSE)
}

# Hieu chinh Holm cho nhieu p-value.
holm_adjust <- function(p) {
  p <- as.numeric(p)
  ok <- !is.na(p)
  if (!any(ok)) return(p)
  q <- rep(NA_real_, length(p))
  pp <- p[ok]
  n <- length(pp)
  o <- order(pp)
  adj <- numeric(n)
  running <- 0
  for (i in seq_len(n)) {
    v <- (n - i + 1) * pp[o[i]]
    running <- max(running, v)
    adj[o[i]] <- min(running, 1)
  }
  q[ok] <- adj
  q
}

# ---------------------------------------------------------------------------
# 7. TONG HOP: danh gia MOT NHOM ban ghi du bao
# ---------------------------------------------------------------------------
# `evaluate_predictions(pred, obs, exceed_thresholds = NULL, obs_exceed = NULL)`
# Tra ve danh sach cac bang ket qua:
#   $overall   : chi so tong hop theo model
#   $by_lead   : theo lead_day
#   $by_peak   : theo peak_idx
#   $by_station: theo tram
#   $contingency: POD/FAR/CSI
#   $tests     : Wilcoxon + DM + Holm
evaluate_predictions <- function(pred, obs, exceed_thresholds = NULL,
                                 obs_exceed = NULL, model_a = NULL) {
  matched <- match_forecast_observed(pred, obs, window_h = 6)
  if (!nrow(matched)) {
    return(list(matched = matched, overall = NULL, by_lead = NULL,
                by_peak = NULL, by_station = NULL, contingency = NULL, tests = NULL))
  }
  per_model <- split(matched, matched$model)
  ov <- list(); bl <- list(); bp <- list(); bs <- list(); ct <- list()
  for (mn in names(per_model)) {
    d <- per_model[[mn]]
    ov[[mn]] <- cbind(model = mn, compute_metrics(d))
    for (lv in sort(unique(d$lead_day))) {
      s <- d[!is.na(d$lead_day) & d$lead_day == lv, , drop = FALSE]
      r <- compute_metrics(s)
      if (!is.null(r)) bl[[length(bl) + 1L]] <- cbind(model = mn, lead_day = lv, r)
    }
    for (pi in sort(unique(d$peak_idx))) {
      s <- d[!is.na(d$peak_idx) & d$peak_idx == pi, , drop = FALSE]
      r <- compute_metrics(s)
      if (!is.null(r)) bp[[length(bp) + 1L]] <- cbind(model = mn, peak_idx = pi, r)
    }
    for (stn in sort(unique(d$station))) {
      s <- d[d$station == stn, , drop = FALSE]
      r <- compute_metrics(s)
      if (!is.null(r)) bs[[length(bs) + 1L]] <- cbind(model = mn, station = stn, r)
    }
    if (!is.null(obs_exceed)) {
      cts <- contingency_scores(d, obs_exceed, thresholds = exceed_thresholds)
      if (!is.null(cts) && nrow(cts)) {
        ct[[length(ct) + 1L]] <- cbind(model = mn, cts)
      }
    }
  }
  res <- list(matched = matched,
              overall = do.call(rbind, ov),
              by_lead = if (length(bl)) do.call(rbind, bl) else NULL,
              by_peak = if (length(bp)) do.call(rbind, bp) else NULL,
              by_station = if (length(bs)) do.call(rbind, bs) else NULL,
              contingency = if (length(ct)) do.call(rbind, ct) else NULL,
              tests = NULL)
  # Kiem dinh so sanh giua cac mo hinh (neu co >= 2 mo hinh)
  if (length(per_model) >= 2) {
    tn <- names(per_model)
    wr <- list(); dmr <- list()
    for (i in seq_along(tn)) for (j in seq_along(tn)) {
      if (i >= j) next
      w <- wilcoxon_paired(per_model[[tn[i]]], per_model[[tn[j]]])
      if (!is.null(w)) {
        w$model_a <- tn[i]; w$model_b <- tn[j]
        wr[[length(wr) + 1L]] <- w
      }
      # Diebold-Mariano tren hieu sai do (a - b) theo cap CHUNG
      ka <- paste(per_model[[tn[i]]]$station, per_model[[tn[i]]]$target_date,
                  per_model[[tn[i]]]$peak_idx, sep = "|")
      kb <- paste(per_model[[tn[j]]]$station, per_model[[tn[j]]]$target_date,
                  per_model[[tn[j]]]$peak_idx, sep = "|")
      cm <- intersect(ka, kb)
      if (length(cm) >= 20) {
        da <- per_model[[tn[i]]]$d_h[match(cm, ka)]
        db <- per_model[[tn[j]]]$d_h[match(cm, kb)]
        dm <- dm_test(da - db, loss = "abs")
        if (!is.null(dm)) {
          dm$model_a <- tn[i]; dm$model_b <- tn[j]
          dmr[[length(dmr) + 1L]] <- dm
        }
      }
    }
    if (length(wr)) {
      W <- do.call(rbind, wr)
      W$p_holm <- holm_adjust(W$p_value)
      res$tests$wilcoxon <- W
    }
    if (length(dmr)) {
      D <- do.call(rbind, dmr)
      D$p_holm <- holm_adjust(D$p_value)
      res$tests$dm <- D
    }
  }
  res
}
