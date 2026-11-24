#!/usr/bin/env Rscript
# harmonic.R -- Phan tich dieu hoa thuy trieu (Chuong 4, muc 4.3).
#
#   Rscript pipelines/r/harmonic.R
#
# LAM GI:
#  1. Doc `tide_extremes` (qc = "ok", khong "suspect") + `hydromet_bulletins`
#     de xac dinh NGAY QUAN SAT cua tung dinh/chan (quy tac dung chung voi
#     `pipelines/spark/features/rules.py::peak_day_offset`).
#  2. Fit mo hinh dieu hoa BOC NHIEU BAY PHUONG NHO GOC cho tung tram
#     (xem `R/harmonics.R` de biet ly do chon phuong phap).
#  3. Bao cao he so (bien do, pha) cua cac thanh phan M2, S2, K1, O1, N2...
#  4. Kiem tra do chinh xac: fit tren TAP TRAIN, du tinh tren TAP VAL (bien lay
#     tu `config/splits.json`; KHONG dung tap test), tinh MAE do cao (m) + MAE gio (h).
#  5. Sinh TRIEU THIEN VAN DU TINH cho toan giai doan + 5 ngay tuong lai,
#     ghi ra `data/processed/features/astro_tide.csv` (cot day,h1,h2,t1_h,t2_h)
#     -- day la file job Spark G5 doc de dien `astro_*`.
#     He so dieu hoa CHI fit tren cac tap `harmonic_fit_splits` cua
#     `config/splits.json` (mac dinh: train) -- khong bao gio cham tap test
#     (kiem tra tu dong `assert_no_test_days`). Ghi kem
#     `data/processed/features/astro_tide_meta.json` (fit_from/fit_to) de G5 va
#     phasegnn tu choi file fit tren tap test.
#  6. Luu PHAN DU PHI THIEN VAN = thuc do - thien van, theo tram - ngay.
#
# DAU RA:
#   data/processed/features/astro_tide.csv     (dinh dang cho Spark G5)
#   data/processed/features/astro_tide_meta.json (giai doan fit, moc test)
#   results/E1/harmonic_components.csv         (he so thanh phan)
#   results/E1/harmonic_fit_summary.csv        (tong hop theo tram)
#   results/E1/harmonic_validation.csv         (MAE do cao / MAE gio)
#   results/E1/astro_residual.csv              (phan du phi thien van)
#   results/E1/harmonic_*.png                  (hinh)
#
# Luat phu thuoc (ADR 0001): khong goi Python, khong import tu `packages/`.

# ---------------------------------------------------------------------------
# 0. Moi truong
# ---------------------------------------------------------------------------
options(stringsAsFactors = FALSE)
Sys.setenv(TZ = "Asia/Ho_Chi_Minh")

here <- tryCatch({
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f[1], winslash = "/")) else getwd()
}, error = function(e) getwd())

# `here` = thu muc chua script (thuong la `<repo>/pipelines/r`).
# Tim goc repo: day len `here` cho den khi gap thu muc co `.rlib` hoac `pipelines`.
find_repo_root <- function(start) {
  d <- normalizePath(start, winslash = "/")
  for (i in 1:6) {
    if (dir.exists(file.path(d, "pipelines")) &&
        (dir.exists(file.path(d, ".rlib")) || dir.exists(file.path(d, "data")))) {
      return(d)
    }
    p <- dirname(d)
    if (identical(p, d)) break
    d <- p
  }
  normalizePath(start, winslash = "/")
}
repo <- Sys.getenv("PGNN_REPO_ROOT", unset = "")
if (!nzchar(repo) || !dir.exists(repo)) repo <- find_repo_root(here)
Sys.setenv(PGNN_REPO_ROOT = repo)

source(file.path(here, "R", "connect.R"))
source(file.path(here, "R", "io.R"))
source(file.path(here, "R", "rules.R"))
source(file.path(here, "R", "harmonics.R"))
source(file.path(here, "R", "theme.R"))

use_repo_rlib(repo)
suppressPackageStartupMessages({
  library(ggplot2)
})

OUT_E1 <- p_res("E1")
ensure_dir(OUT_E1)

# ---------------------------------------------------------------------------
# 1. Tham so
# ---------------------------------------------------------------------------
# Cac thanh phan dung cho "bang chung" (bao cao day du).
COMPONENTS_REPORT <- c("M2", "S2", "N2", "K1", "O1", "P1", "Q1", "M4", "MS4", "M6", "MK3")
# Thanh phan dung khi SINH trieu du tinh: bo qua bien (Mf) va bien dai (SSA) --
# bien do nho, gay nhieu doi so, lam nhieu dot sai. Giu M2, S2, N2, K1, O1, P1,
# Q1 + song bien M4, MS4, M6, MK3.
COMPONENTS_FORECAST <- COMPONENTS_REPORT

# Giai doan fit he so dieu hoa: CHI cac tap `harmonic_fit_splits` trong
# `config/splits.json` (mac dinh "train" = 2017-01-01 -> 2022-12-31). Chon
# "train" (khong gop val) vi PhaseGNN/B1 dung val de dung som/chon mo hinh: neu
# fit dieu hoa tren val thi dac trung `astro_*` cua tap val la "trong mau".
# Mo'c test lay tu cung file -> khong viet cung o hai noi.
SPLIT_CFG <- load_splits()
FIT_WINDOW <- harmonic_fit_window(SPLIT_CFG)
TEST_FROM <- SPLIT_CFG$test_from

# So ngay du tinh them (5 ngay tuong lai nhu yeu cau).
FORECAST_DAYS <- 5

# Kiem tra do chinh xac: fit tren train, du tinh tren val (tach theo THOI GIAN,
# khong ngau nhien). Tap test KHONG dung o day.
TRAIN_WINDOW <- SPLIT_CFG$splits$train
VALID_WINDOW <- SPLIT_CFG$splits$val
say2("Giai doan fit dieu hoa: ", FIT_WINDOW[1], " -> ", FIT_WINDOW[2],
     " (tap: ", paste(SPLIT_CFG$harmonic_fit_splits, collapse = "+"), "); mo'c test: ", TEST_FROM)

# ---------------------------------------------------------------------------
# 2. Doc du lieu
# ---------------------------------------------------------------------------
say2("== Doc du lieu trieu ==")
tide <- load_tide_extremes(qc_ok_only = TRUE)
say2("tide_extremes (qc=ok): ", nrow(tide), " phan tu; ",
     length(unique(tide$station)), " tram; ngay ",
     min(tide$ts_day), " -> ", max(tide$ts_day))

bul <- load_bulletin_days()
say2("ban tin chinh: ", nrow(bul), " ban tin; ngay quan sat ",
     min(bul$obs_day), " -> ", max(bul$obs_day))

pt <- build_peak_table(tide, bul)
say2("bang dinh/chan: ", nrow(pt), " dong")

# Tach thanh phan Dinh (high) va CHAN (low) cho viec fit.
# Phuong phap: fit CHUNG mot mo hinh cho ca dinh va chan cua mot tram
# (cung thuoc trieu, cung bien do) -> dung mot mo hinh cho ca chuoi muc thuy.
fit_data <- pt[, c("station", "kind", "ts", "obs_day", "ts_day", "value", "rn")]
fit_data <- fit_data[is.na(fit_data$value) == FALSE, ]
fit_data$year <- as.integer(substr(fit_data$obs_day, 1, 4))

say2("\n== Fit dieu hoa theo tram ==")
fits <- list()
comp_rows <- list()
for (st in sort(unique(fit_data$station))) {
  d_all <- fit_data[fit_data$station == st, ]
  d <- d_all[in_fit_window(d_all$obs_day, FIT_WINDOW), ]
  if (nrow(d) < 500) {
    say2("  ", st, ": bo qua (chi ", nrow(d), " moc)")
    next
  }
  assert_no_test_days(d$obs_day, TEST_FROM, paste("fit dieu hoa", st))
  t <- t_ref(d$ts)
  fit <- fit_harmonics(t, d$value, components = COMPONENTS_REPORT, trend = TRUE)
  # `t/y/obs_day/ts/kind`: TOAN BO chuoi cua tram (dung cho kiem tra va phan du);
  # chi `fit` la tu giai doan FIT_WINDOW.
  fits[[st]] <- list(fit = fit, t = t_ref(d_all$ts), y = d_all$value, station = st,
                     obs_day = d_all$obs_day, ts = d_all$ts, kind = d_all$kind,
                     fit_from = min(d$obs_day), fit_to = max(d$obs_day))
  for (i in seq_along(COMPONENTS_REPORT)) {
    comp_rows[[length(comp_rows) + 1L]] <- data.frame(
      station = st, component = COMPONENTS_REPORT[i],
      period_h = HARMONIC_PERIOD_H[[COMPONENTS_REPORT[i]]],
      amplitude_m = fit$amplitude[i], phase_deg = fit$phase_deg[i],
      var_expl = fit$var_expl, rms_resid_m = fit$rms_resid,
      n_obs = fit$n, stringsAsFactors = FALSE)
  }
  say2(sprintf("  %-12s n=%6d  var_expl=%.4f  rms=%.4f m  M2=%.3f m  K1=%.3f m",
               st, fit$n, fit$var_expl, fit$rms_resid,
               fit$amplitude[["M2"]], fit$amplitude[["K1"]]))
}

comp_df <- do.call(rbind, comp_rows)

# ---------------------------------------------------------------------------
# 3. Tong hop he so thanh phan
# ---------------------------------------------------------------------------
# Bang chung: bien do (m) va pha (do) cua M2, S2, K1, O1, N2 cho tung tram.
evidence <- comp_df[comp_df$component %in% c("M2", "S2", "K1", "O1", "N2"), ]
write_csv_df(comp_df, file.path(OUT_E1, "harmonic_components.csv"))

fit_sum <- do.call(rbind, lapply(names(fits), function(st) {
  f <- fits[[st]]$fit
  data.frame(station = st, fit_from = fits[[st]]$fit_from, fit_to = fits[[st]]$fit_to,
             n_obs = f$n, df = f$df,
             var_expl = f$var_expl, rms_resid_m = f$rms_resid,
             a0_m = unname(f$coef[["a0"]]),
             amp_M2_m = f$amplitude[["M2"]],
             amp_K1_m = f$amplitude[["K1"]],
             amp_O1_m = f$amplitude[["O1"]],
             stringsAsFactors = FALSE)
}))
write_csv_df(fit_sum, file.path(OUT_E1, "harmonic_fit_summary.csv"))
say2("\nTong hop fit: ", nrow(fit_sum), " tram")

# ---------------------------------------------------------------------------
# 4. Kiem tra do chinh xac: tach theo NAM
# ---------------------------------------------------------------------------
# Cach kiem tra:
#  - Fit tren TAP TRAIN, du tinh tren TAP VAL (`config/splits.json`).
#  - Do chi sai nguoi quan sat (MAE do cao, m) va MAE gio dinh (h).
#
# Cach do MAE GIO: so sanh gio dinh thuc do voi gio dinh du tinh cua cung
# ngay. Vi trieu co 2 dinh/ngay, dinh du tinh duoc chon theo "gan nhat voi gio
# thuc do" de tranh ho doi dinh 1 <-> dinh 2 (nguon sai so lon nhat).
say2("\n== Kiem tra do chinh xac (fit train -> du tinh val) ==")
val_rows <- list()
for (st in names(fits)) {
  d <- fits[[st]]
  dd <- d$obs_day
  in_tr <- in_fit_window(dd, TRAIN_WINDOW)
  in_va <- in_fit_window(dd, VALID_WINDOW)
  assert_no_test_days(dd[in_tr | in_va], TEST_FROM, paste("kiem tra dieu hoa", st))
  tr <- d$t[in_tr]
  yv <- d$y[in_va]
  tv <- d$t[in_va]
  dvv <- dd[in_va]
  kv <- d$kind[in_va]
  tsv <- d$ts[in_va]
  if (length(yv) < 50) next
  fitv <- fit_harmonics(tr, d$y[in_tr],
                        components = COMPONENTS_FORECAST, trend = TRUE)
  # du tinh toan bo moc kiem tra
  pred <- astro_at(tv, fitv)
  res <- yv - pred
  mae_h <- mean(abs(res), na.rm = TRUE)
  rmse_h <- sqrt(mean(res^2, na.rm = TRUE))
  # MAE gio dinh: khop moi dinh THUC DO voi dinh DU TINH cung ngay.
  # Cach khop: dua ve cung nua dem (buoi sang / buoi chieu) de khong nham
  # dinh 1 <-> dinh 2 (nguon sai so lon nhat), roi chon dinh du tinh cung
  # nua dem gan nhat voi gio thuc do (so sanh theo vong 24 gio).
  hours <- NA_real_
  pk <- kv == "high"
  if (any(pk)) {
    dfp <- data.frame(day = dvv[pk], t = tv[pk], obs = yv[pk],
                      ts = tsv[pk], stringsAsFactors = FALSE)
    errs_h <- c(); errs_d <- c()
    for (day in unique(dfp$day)) {
      o <- dfp[dfp$day == day, , drop = FALSE]
      t0 <- as.POSIXct(paste0(day, " 00:00:00"), tz = "Asia/Ho_Chi_Minh",
                   format = "%Y-%m-%d %H:%M:%S")
      tt <- seq(t0, t0 + 86400 - 60, by = 60)
      pr <- astro_at(t_ref(tt), fitv)
      hr <- as.numeric(difftime(tt, t0, units = "hours"))
      isloc <- which(pr >= c(-Inf, utils::head(pr, -1)) & pr >= c(utils::tail(pr, -1), -Inf))
      if (!length(isloc)) isloc <- which(pr >= max(pr) - 0.2)
      if (!length(isloc)) next
      for (i in seq_len(nrow(o))) {
        h_obs <- as.numeric(difftime(o$ts[i], t0, units = "hours"))
        if (!is.finite(h_obs)) next
        half_obs <- if (h_obs < 12) "AM" else "PM"
        same_half <- isloc[(hr[isloc] < 12) == (half_obs == "AM")]
        if (!length(same_half)) same_half <- isloc
        dh <- abs(hr[same_half] - h_obs)
        dh2 <- pmin(dh, 24 - dh)
        kbest <- which.min(dh2)
        errs_h <- c(errs_h, dh2[kbest])
        errs_d <- c(errs_d, abs(o$obs[i] - pr[same_half[kbest]]))
      }
    }
    if (length(errs_h)) hours <- mean(errs_h)
  }
  val_rows[[length(val_rows) + 1L]] <- data.frame(
    station = st, n_val = length(yv),
    train_window = paste(TRAIN_WINDOW, collapse = "/"),
    val_window = paste(VALID_WINDOW, collapse = "/"),
    mae_height_m = mae_h, rmse_height_m = rmse_h,
    mae_peak_hour_h = hours, stringsAsFactors = FALSE)
  say2(sprintf("  %-12s n_val=%6d  MAE cao=%.4f m  RMSE=%.4f m  MAE gio=%.3f h",
               st, length(yv), mae_h, rmse_h, hours))
}
val_df <- do.call(rbind, val_rows)
write_csv_df(val_df, file.path(OUT_E1, "harmonic_validation.csv"))

# ---------------------------------------------------------------------------
# 5. Sinh trieu thien van du tinh -> astro_tide.csv
# ---------------------------------------------------------------------------
# DINH NGHIA COT (khop voi `pipelines/spark/features/build_training_set.py`):
#   day   : ngay quan sat (YYYY-MM-DD), gio Viet Nam
#   h1,h2 : do cao dinh thien van du tinh thu 1 / thu 2 trong ngay (m)
#   t1_h,t2_h : gio dinh (0..24) cua dinh 1 / dinh 2
#
# QUAN TRONG: `h1`,`h2` la DINH (khong phai chung cua dinh/cham). Job G5 ghep
# `astro_h1` vao dong theo `day` (khong theo tram) -> file nay phai la TRIEU
# THIEN VAN TAI VUNG TAU (tram goc cua song trieu), dung nguon cho ca luu vuc.
# Cac tram khac se duoc xu ly o `phase_lag.R` (do tre pha + bien do theo tram).
say2("\n== Sinh trieu thien van du tinh ==")
REF_STATION <- "VUNG_TAU"
if (!REF_STATION %in% names(fits)) stop("Khong co tram tham chieu ", REF_STATION)

ref <- fits[[REF_STATION]]
# Khoang thoi gian can du tinh: tu ngay quan sat dau tien den ngay cuoi + 5 ngay
d0 <- min(as.Date(fit_data$obs_day))
d1 <- max(as.Date(fit_data$obs_day))
days <- seq(d0, d1 + FORECAST_DAYS, by = "day")
days_ch <- format(days, "%Y-%m-%d")

# Moi ngay: quet moi phut de tim 2 dinh
scan_min <- 1L
astro_rows <- vector("list", length(days_ch))
for (k in seq_along(days_ch)) {
  day <- days_ch[k]
  t0 <- as.POSIXct(paste0(day, " 00:00:00"), tz = "Asia/Ho_Chi_Minh",
                   format = "%Y-%m-%d %H:%M:%S")
  tt <- seq(t0, t0 + 86400 - 60, by = 60)
  pr <- astro_at(t_ref(tt), ref$fit)
  # tim cuc loi dai (dinh) bo qua 20 phut dau/cuoi de tranh nhieu bien
  ok <- (as.numeric(tt - t0) >= 20 * 60) & (as.numeric(tt - t0) <= 86400 - 20 * 60)
  idx <- which(ok)
  if (!length(idx)) next
  pv <- pr[idx]; ti <- tt[idx]
  # cuc loi dai (dao dau)
  loc <- which(pv >= c(-Inf, utils::head(pv, -1)) & pv >= c(utils::tail(pv, -1), -Inf))
  if (!length(loc)) {
    # fallback: 2 gia tri lon nhat cach nhau >= 4 gio
    o <- order(pv, decreasing = TRUE)
    sel <- integer(0)
    for (j in o) {
      if (!length(sel) || all(abs(as.numeric(difftime(ti[j], ti[sel], units = "hours"))) > 4)) {
        sel <- c(sel, j)
        if (length(sel) == 2) break
      }
    }
    loc <- sel
  }
  if (!length(loc)) next
  if (length(loc) > 2) {
    pv2 <- pv[loc]
    o <- order(pv2, decreasing = TRUE)
    loc <- loc[o[1:2]]
  }
  hh <- as.numeric(difftime(ti[loc], t0, units = "hours"))
  hgt <- pv[loc]
  o2 <- order(hh)
  astro_rows[[k]] <- data.frame(day = day, h1 = hgt[o2[1]], h2 = hgt[o2[length(o2)]],
                                t1_h = hh[o2[1]], t2_h = hh[o2[length(o2)]],
                                stringsAsFactors = FALSE)
}
astro <- do.call(rbind, astro_rows[!vapply(astro_rows, is.null, logical(1))])
astro <- astro[order(astro$day), ]
astro$h1 <- round(astro$h1, 4); astro$h2 <- round(astro$h2, 4)
astro$t1_h <- round(astro$t1_h, 4); astro$t2_h <- round(astro$t2_h, 4)

astro_path <- p_data("processed", "features", "astro_tide.csv")
write_csv_df(astro, astro_path)
say2("ghi ", astro_path, ": ", nrow(astro), " dong (tu ", min(astro$day), " den ",
     max(astro$day), ")")
# Meta: giai doan fit -> G5 (`features/splits.py::astro_leak_reason`) va phasegnn
# (`check_astro_provenance`) tu choi file neu fit_to >= mo'c test.
fit_to_all <- max(vapply(fits, function(f) f$fit_to, ""))
fit_from_all <- min(vapply(fits, function(f) f$fit_from, ""))
assert_no_test_days(fit_to_all, TEST_FROM, "astro_tide_meta")
astro_meta_path <- p_data("processed", "features", "astro_tide_meta.json")
write_json_df(list(
  fit_from = fit_from_all, fit_to = fit_to_all,
  fit_splits = I(SPLIT_CFG$harmonic_fit_splits),
  fit_window = FIT_WINDOW, test_from = TEST_FROM,
  splits_file = "config/splits.json",
  components = COMPONENTS_REPORT, trend = TRUE,
  files = c("data/processed/features/astro_tide.csv", "results/E1/astro_tide_by_station.csv"),
  generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
  generator = "pipelines/r/harmonic.R"), astro_meta_path)
say2("ghi ", astro_meta_path, ": fit ", fit_from_all, " -> ", fit_to_all, " (< test ", TEST_FROM, ")")

# ---------------------------------------------------------------------------
# 5b. TRIEU THIEN VAN THEO TUNG TRAM -> results/E1/astro_tide_by_station.csv
# ---------------------------------------------------------------------------
# `astro_tide.csv` (theo hop dong du lieu voi Spark G5) chi chua TRIEU THIEN VAN
# TAI VUNG TAU. De danh gia cong bang cho TUNG TRAM (va de hien phan du thi thien
# van), ta sinh them bang trieu thien van cho MOI tram co fit harmonic.
#
# File nay KHONG thay the `astro_tide.csv`; no chi phuc vu cho `evaluation.R`
# va bao cao Chương 4.
say2("\n== Sinh trieu thien van du tinh theo tram ==")
astro_st_rows <- vector("list", 0L)
for (st in names(fits)) {
  f <- fits[[st]]$fit
  rows_st <- vector("list", length(days_ch))
  for (k in seq_along(days_ch)) {
    day <- days_ch[k]
    t0 <- as.POSIXct(paste0(day, " 00:00:00"), tz = "Asia/Ho_Chi_Minh",
                     format = "%Y-%m-%d %H:%M:%S")
    tt <- seq(t0, t0 + 86400 - 60, by = 60)
    pr <- astro_at(t_ref(tt), f)
    ok <- (as.numeric(tt - t0) >= 20 * 60) &
      (as.numeric(tt - t0) <= 86400 - 20 * 60)
    idx <- which(ok)
    if (!length(idx)) next
    pv <- pr[idx]; ti <- tt[idx]
    loc <- which(pv >= c(-Inf, utils::head(pv, -1)) &
                   pv >= c(utils::tail(pv, -1), -Inf))
    if (!length(loc)) {
      o <- order(pv, decreasing = TRUE)
      sel <- integer(0)
      for (j in o) {
        if (!length(sel) ||
            all(abs(as.numeric(difftime(ti[j], ti[sel], units = "hours"))) > 4)) {
          sel <- c(sel, j)
          if (length(sel) == 2) break
        }
      }
      loc <- sel
    }
    if (!length(loc)) next
    if (length(loc) > 2) {
      pv2 <- pv[loc]
      o <- order(pv2, decreasing = TRUE)
      loc <- loc[o[1:2]]
    }
    hh <- as.numeric(difftime(ti[loc], t0, units = "hours"))
    hgt <- pv[loc]
    o2 <- order(hh)
    rows_st[[k]] <- data.frame(
      station = st, day = day,
      h1 = hgt[o2[1]], h2 = hgt[o2[length(o2)]],
      t1_h = hh[o2[1]], t2_h = hh[o2[length(o2)]],
      stringsAsFactors = FALSE)
  }
  keep <- !vapply(rows_st, is.null, logical(1))
  if (any(keep)) astro_st_rows[[length(astro_st_rows) + 1L]] <-
    do.call(rbind, rows_st[keep])
}
if (length(astro_st_rows)) {
  astro_st <- do.call(rbind, astro_st_rows)
  astro_st$h1 <- round(astro_st$h1, 4); astro_st$h2 <- round(astro_st$h2, 4)
  astro_st$t1_h <- round(astro_st$t1_h, 4); astro_st$t2_h <- round(astro_st$t2_h, 4)
  astro_st <- astro_st[order(astro_st$station, astro_st$day), ]
  rownames(astro_st) <- NULL
  write_csv_df(astro_st, file.path(OUT_E1, "astro_tide_by_station.csv"))
  say2("ghi astro_tide_by_station.csv: ", nrow(astro_st), " dong (",
       length(unique(astro_st$station)), " tram)")
}

# ---------------------------------------------------------------------------
# 6. Phan du phi thien van (thuc do - thien van), theo tram - ngay
# ---------------------------------------------------------------------------
# Voi MOI tram: lay cac dinh thuc do (high) trong ngay, du tinh muc thuyen van
# tai dung moc do, phan du = thuc do - thien van. Luu theo (tram, ngay).
say2("\n== Phan du phi thien van ==")
res_rows <- list()
for (st in names(fits)) {
  d <- fits[[st]]
  pk <- d$kind == "high"
  if (!any(pk)) next
  pr <- astro_at(d$t[pk], d$fit)
  # NHAN VAT: "ngay" cua phan du = ngay quan sat (cung quy tac voi G5).
  rr <- data.frame(station = st, day = d$obs_day[pk],
                   obs_h = d$y[pk], astro_h = pr,
                   residual_m = d$y[pk] - pr, stringsAsFactors = FALSE)
  res_rows[[length(res_rows) + 1L]] <- rr
}
resid <- do.call(rbind, res_rows)
write_csv_df(resid, file.path(OUT_E1, "astro_residual.csv"))
say2("ghi astro_residual.csv: ", nrow(resid), " dong (tram x ngay x dinh)")

# ---------------------------------------------------------------------------
# 7. Hinh
# ---------------------------------------------------------------------------
# 7.1 Bien do thanh phan theo tram
comp_ev <- comp_df[comp_df$component %in% c("M2", "S2", "K1", "O1", "N2"), ]
p_amp <- ggplot(comp_ev, aes(x = station, y = amplitude_m, fill = component)) +
  geom_col(position = position_dodge(width = 0.85), width = 0.8) +
  scale_fill_manual(values = palette_pgnn(5)) +
  labs(title = "Bien do thanh phan dieu hoa theo tram",
       subtitle = paste0("Bo nhieu bay phuong nho goc (LSQ), dinh/chan ", FIT_WINDOW[1], " -> ",
                         FIT_WINDOW[2], " (chi tap fit, khong gom test)"),
       x = NULL, y = "Bien do (m)", fill = "Thanh phan") +
  theme_pgnn()
save_fig(print(p_amp), file.path(OUT_E1, "harmonic_amplitudes.png"), width = 1600, height = 880)

# 7.2 Chu ky cua trieu du tinh tai Vung Tau (30 ngay dau)
d0h <- as.Date(min(fit_data$obs_day))
t0h <- as.POSIXct(paste0(format(d0h, "%Y-%m-%d"), " 00:00:00"), tz = "Asia/Ho_Chi_Minh",
                    format = "%Y-%m-%d %H:%M:%S")
th <- seq(t0h, t0h + 30 * 86400, by = 1800)
ph <- astro_at(t_ref(th), ref$fit)
dfr <- data.frame(ts = th, h = ph)
p_tide <- ggplot(dfr, aes(x = ts, y = h)) +
  geom_line(color = palette_pgnn(1), linewidth = 0.4) +
  labs(title = "Trieu thien van du tinh tai Vung Tau (30 ngay dau)",
       subtitle = "Mo hinh dieu hoa bo nhieu bay phuong nho goc (11 thanh phan)",
       x = NULL, y = "Muc thuy du tinh (m)") +
  theme_pgnn() +
  theme(axis.text.x = element_text(angle = 30, hjust = 1))
save_fig(print(p_tide), file.path(OUT_E1, "harmonic_astro_tide_vungtau.png"), width = 1760, height = 720)

# 7.3 So sanh dinh thuc do vs du tinh (Vung Tau, 60 ngay)
p_dat <- fit_data[fit_data$station == REF_STATION &
                   fit_data$kind == "high" &
                   as.Date(fit_data$obs_day) >= d0h &
                   as.Date(fit_data$obs_day) < d0h + 60, , drop = FALSE]
if (nrow(p_dat)) {
  p_dat$pred <- astro_at(t_ref(p_dat$ts), ref$fit)
  p_dat$ts_vn <- p_dat$ts
  p_cmp <- ggplot(p_dat, aes(x = ts_vn, y = value)) +
    geom_point(color = palette_pgnn1(2), size = 1.1) +
    geom_line(aes(y = pred), color = palette_pgnn(1), linewidth = 0.5) +
    labs(title = "Dinh trieu: thuc do vs thien van du tinh (Vung Tau, 60 ngay dau)",
         subtitle = "Diem: dinh thuc do (qc=ok) - Duong: muc thuyen van du tinh",
         x = NULL, y = "Muc thuy (m)") +
    theme_pgnn() +
    theme(axis.text.x = element_text(angle = 30, hjust = 1))
  save_fig(print(p_cmp), file.path(OUT_E1, "harmonic_observed_vs_astro.png"),
           width = 1760, height = 720)
}

# 7.4 Phan du phi thien van theo tram (boxplot)
p_res <- ggplot(resid, aes(x = station, y = residual_m)) +
  geom_hline(yintercept = 0, color = "#888888", linetype = "dashed", linewidth = 0.4) +
  geom_boxplot(outlier.shape = NA, fill = palette_pgnn(1), alpha = 0.6) +
  labs(title = "Phan du phi thien van theo tram (thuc do - thien van)",
       subtitle = "Dinh trieu thuc do truoc muc thuyen van du tinh",
       x = NULL, y = "Phan du (m)") +
  theme_pgnn()
  save_fig(print(p_res), file.path(OUT_E1, "astro_residual_by_station.png"), width = 1600, height = 800)

# 7.5 MAE kiem tra theo tram
if (nrow(val_df)) {
  p_val <- ggplot(val_df, aes(x = station, y = mae_height_m)) +
    geom_col(fill = palette_pgnn(1), width = 0.7) +
    geom_text(aes(label = sprintf("%.3f", mae_height_m)), vjust = -0.4, size = 3.4) +
    scale_y_continuous(expand = expansion(mult = c(0, 0.15))) +
    labs(title = paste0("MAE do cao kiem tra (tap val ", VALID_WINDOW[1], " -> ", VALID_WINDOW[2], ")"),
         subtitle = "Fit tren tap train; MAE tren toan bo moc cua tap val",
         x = NULL, y = "MAE (m)") +
    theme_pgnn()
  save_fig(print(p_val), file.path(OUT_E1, "harmonic_validation_mae.png"), width = 1440, height = 800)
}

# ---------------------------------------------------------------------------
# 8. Bao cao ket qua
# ---------------------------------------------------------------------------
say2("\n================ KET QUA harmonic.R ================")
say2("tide_extremes dung (qc=ok): ", nrow(tide))
say2("tram co fit: ", length(fits), " -> ", paste(names(fits), collapse = ", "))
say2("he so thanh phan: ", nrow(comp_df), " dong")
say2("MAE do cao trung binh: ", sprintf("%.4f m", mean(val_df$mae_height_m, na.rm = TRUE)))
say2("MAE gio trung binh: ", sprintf("%.3f h", mean(val_df$mae_peak_hour_h, na.rm = TRUE)))
say2("astro_tide.csv: ", nrow(astro), " dong")
say2("astro_residual.csv: ", nrow(resid), " dong")
say2("====================================================")
