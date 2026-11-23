#!/usr/bin/env Rscript
# phase_lag.R -- Do tre pha trieu (Chuong 4, muc 4.4) va cac gia thuyet H5-H7.
#
#   Rscript pipelines/r/phase_lag.R
#
# LAM GI:
#  1. DOC TRIEU THUC DO (dinh/chan) va trich MOI DINH theo (tram, ngay).
#  2. DO TRE PHA so voi VUNG TAU:
#       - Uoc luong CHINH: tuyet so goc tuong quan CHEO (cross-correlation)
#         giua chuoi gio dinh cua tram voi chuoi gio dinh VUNG TAU, tim
#         lech (lag) toi da toi uu trong cua so [-L, +L] gio.
#       - Uoc luong BO SUNG: chenh lech PHA cua thanh phan dieu hoa M2/S2
#         (tu `harmonic.R`) -> doi sang gio.
#       - Moi tram: trung vị, IQR, khoang tin cay bootstrap (theo NGAY).
#  3. H5: hoi quy do tre theo KHOANG CACH DOC SONG (`stations.merit.dist_to_root_km`).
#  4. H6: do tre theo MUA va theo LUU LUONG XA HO (`lag ~ Q_release + season`).
#  5. H7: ty so bien do tram / VUNG TAU theo khoang cach.
#  6. Tuong quan CHEO XA HO (Tri An, Dau Tien) voi PHAN DU PHI THIEN VAN
#     (do tre 0-5 ngay).
#
# DAU RA -> results/E1/:
#   phase_lag_by_station.csv     do tre tung tram (trung vị, IQR, CI bootstrap)
#   phase_lag_method_compare.csv so sanh 2 phuong phap uoc luong do tre
#   h5_lag_vs_distance.csv       hoi quy H5
#   h6_lag_vs_discharge.csv      hoi quy H6
#   h7_amplitude_ratio.csv       H7
#   crosscorr_release_residual.csv tuong quan cheo xa ho <-> phan du phi thien van
#   phase_lag_*.png              hinh
#
# Luat phu thuoc (ADR 0001): khong goi Python, khong import tu `packages/`.

options(stringsAsFactors = FALSE)
Sys.setenv(TZ = "Asia/Ho_Chi_Minh")

here <- tryCatch({
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f[1], winslash = "/")) else getwd()
}, error = function(e) getwd())

find_repo_root <- function(start) {
  d <- normalizePath(start, winslash = "/")
  for (i in 1:6) {
    if (dir.exists(file.path(d, "pipelines")) &&
        (dir.exists(file.path(d, ".rlib")) || dir.exists(file.path(d, "data")))) return(d)
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
suppressPackageStartupMessages(library(ggplot2))

OUT_E1 <- p_res("E1")
ensure_dir(OUT_E1)

# ---------------------------------------------------------------------------
# Tham so
# ---------------------------------------------------------------------------
REF_STATION <- "VUNG_TAU"     # tram bien -- goc tham chieu
MAX_LAG_H <- 30               # cua so tim do tre: [-30, +30] gio
N_BOOT <- 2000                # so lan bootstrap cho CI 95%
SEED <- 20261123

# Chu ky tham chieu cho uoc luong do tre theo PHA HARMONIC: M2 (nuoc lon bien).
PHASE_COMPONENT <- "M2"

say2("== Doc du lieu ==")
tide <- load_tide_extremes(qc_ok_only = TRUE)
bul <- load_bulletin_days()
pt <- build_peak_table(tide, bul)
pk <- pivot_extremes(pt, "peak")
say2("din thuc do (tram x ngay): ", nrow(pk), " dong; ", length(unique(pk$station)), " tram")

stations_tbl <- load_stations()
dist_tbl <- station_dist_to_root(stations_tbl)
say2("tram co `merit.dist_to_root_km`: ", sum(!is.na(dist_tbl$dist_to_root_km)), "/", nrow(dist_tbl))

res <- load_reservoir()
say2("reservoir_daily: ", nrow(res), " dong; ", length(unique(res$reservoir)), " ho")

# ---------------------------------------------------------------------------
# 1. CHUOI GIO DINH (dinh thu N) THEO (tram, ngay)
# ---------------------------------------------------------------------------
# Voi moi (tram, ngay) ta giu gio dinh thu 1 va thu 2. De uoc luong do tre bang
# tuyet so goc tuong quan cheo, ta can hai chuoi cung do dai va cung buoi.
#
# QUY UOC PHA:
#   - "Dinh chinh" (peak 1) = dinh nuoc lon (thuong la buoi chieu/-toi).
#   - "Dinh phu"  (peak 2) = dinh nuoc nho/thu hai trong ngay.
# Do tre duoc uoc luong RIENG cho tung loai dinh (peak1 vs peak1, peak2 vs peak2)
# de tranh nham (nguon sai so lon nhat), roi gop lai.
#
# Vung tau dong VUNG TAU co 2 dinh/ngay; cac tram con lai cung vay.
peak_series <- function(pk, which = 1L) {
  h <- if (which == 1L) pk$h1 else pk$h2
  t <- if (which == 1L) pk$t1_h else pk$t2_h
  d <- data.frame(station = pk$station, day = pk$day,
                  h = h, t_h = t, stringsAsFactors = FALSE)
  d <- d[!is.na(d$h) & !is.na(d$t_h), , drop = FALSE]
  d <- d[order(d$station, d$day), , drop = FALSE]
  rownames(d) <- NULL
  d
}

ser1 <- peak_series(pk, 1L)
ser2 <- peak_series(pk, 2L)
say2("chuoi dinh 1: ", nrow(ser1), " moc; dinh 2: ", nrow(ser2), " moc")

# ---------------------------------------------------------------------------
# 2. DO TRE PHA: TUYET SO GOC TUONG QUAN CHEO (cross-correlation)
# ---------------------------------------------------------------------------
# HAM `xcorr_lag(t_ref, t_station, lags, max_lag)`:
#   doi chieu hai chuoi theo TUNG CUNG NGAY (chi dung cac ngay co ca hai chuoi),
#   tinh tuong quan tuyen so goc cho moi lech lag:
#       r(lag) = mean( (t_ref - mean(t_ref)) * (t_st - mean(t_st)) ) / (sd * sd)
#
# KY HIEU CHUAN (khop voi `lag_for_station`): lag > 0 => dinh tai TRAM cham
# hon dinh tai VUNG TAU `lag` gio.
#
# CACH DICH CHIEU: tai moi ngay i, so sanh gio dinh VUNG TAU tai ngay i voi
# gio dinh TRAM tai ngay i + L.  =>  t_station[i + L] ~ t_ref[i] + L.
#   (Vung bien truoc -> tram thuong nguon cham hon -> t_station > t_ref khi L > 0.)
xcorr_lag <- function(t_ref, t_station, lags, max_lag) {
  n_ref <- length(t_ref); n_st <- length(t_station)
  out <- rep(NA_real_, length(lags))
  for (k in seq_len(length(lags))) {
    L <- lags[k]
    lo <- max(1L, 1L - L)
    hi <- min(n_ref, n_st - L)
    if (hi - lo + 1L < 30) next
    idx <- lo:hi
    a <- t_ref[idx]                 # gio VUNG TAU tai ngay i
    b <- t_station[idx + L]         # gio TRAM tai ngay i + L
    a <- a - mean(a); b <- b - mean(b)
    sa <- sqrt(sum(a * a)); sb <- sqrt(sum(b * b))
    if (!is.finite(sa) || !is.finite(sb) || sa == 0 || sb == 0) next
    out[k] <- sum(a * b) / (sa * sb)
  }
  out
}

# ---------------------------------------------------------------------------
# 2. DO TRE PHA: CHENH LECH PHA TRON (VON MISES TRUNG BINH)
# ---------------------------------------------------------------------------
# PHUONG PHAP CHINH: chenh lech PHA TRON (circular phase difference).
#
# LY DO CHON (thay cho "mean(a) - mean(b)" thong thuong):
#   - gio dinh la MOT PH so 0..24 -> sai so phai duoc xu ly TRON VONG 24 gio.
#     Neu tinh hieu tuyen so, mot cap (a = 23.75, b = 0.25) cho hieu = +23.5 gio
#     thay vi -0.5 gio -> sai so ngoai le lam hong ket qua.
#   - do tre trieu thuong nho (vài gio) so voi chu ky M2 (12.42 h) va chu ky
#     nua dem (24 h), nen phai quy ve khoang (-12, +12] gio.
#
#   Voi moi cap ngay i:
#       d_i = t_st[i] - t_ref[i]        (hieu gio thuc te)
#       d_i^circ = ((d_i + 12) mod 24) - 12   (quy ve (-12, 12])
#   Do tre uoc luong = TRUNG VI VON MISES cua {d_i^circ}:
#       lag = atan2(mean(sin(2*pi*d/24)), mean(cos(2*pi*d/24))) * 24 / (2*pi)
#   Day la uoc luong "phase lag" chuan cho trieu (cung phai luon).
#
# KY HIEU CHUAN (dung chung cho ca R va mo hinh):
#   lag > 0  => dinh tai TRAM xuat hien CHAM HON dinh tai VUNG TAU `lag` gio
#                (cua song lan truyen nguoc dong: do tre tien).
#   lag < 0  => dinh tai TRAM SOM HON dinh tai VUNG TAU `|lag|` gio.
#   lag = 0  => dinh trung thoi.
#
# PHUONG PHAP BO SUNG: tuyet so goc tuong quan cheo (xcorr) tren gio dinh
# (xem `xcorr_lag`), de doi chieu. Cach nay it on dinh hon voi gio dinh
# luoi theo gio -> chi dung de tham khao.
lag_for_station <- function(station, s_st, s_ref, max_lag = MAX_LAG_H) {
  common <- intersect(s_ref$day, s_st$day)
  if (length(common) < 60) return(NULL)
  a <- s_ref$t_h[match(common, s_ref$day)]
  b <- s_st$t_h[match(common, s_st$day)]
  ok <- !is.na(a) & !is.na(b)
  a <- a[ok]; b <- b[ok]
  if (length(a) < 60) return(NULL)
  # chenh lech trong vong 24 gio; DUNG KY HIEU CHUAN: lag > 0 = tram cham hon
  d <- ((b - a + 12) %% 24) - 12
  # trung vi von Mises (do tre, gio)
  ph <- atan2(mean(sin(2 * pi * d / 24)), mean(cos(2 * pi * d / 24)))
  lag_circ <- ph * 24 / (2 * pi)
  # do lan toan cuc cua pha (0..1): do tap trung cua pha
  R <- sqrt(mean(sin(2 * pi * d / 24))^2 + mean(cos(2 * pi * d / 24))^2)
  # xcorr (tham khao)
  lags <- (-max_lag):max_lag
  r <- xcorr_lag(a, b, lags, max_lag)
  lag_x <- NA_real_; r_x <- NA_real_
  if (!all(is.na(r))) {
    ib <- which.max(r)
    lag_x <- lags[ib]; r_x <- r[ib]
  }
  list(lag = lag_circ, r_concentration = R, n = length(a),
       lag_xcorr = lag_x, r_xcorr = r_x, diffs = d)
}

say2("\n== Do tre pha (chenh lech pha tron) ==")
lag_rows <- list()
ref_ser <- list(`1` = ser1[ser1$station == REF_STATION, , drop = FALSE],
                `2` = ser2[ser2$station == REF_STATION, , drop = FALSE])

for (st in sort(unique(pk$station))) {
  if (st == REF_STATION) next
  for (which in 1:2) {
    s_st <- if (which == 1L) ser1[ser1$station == st, , drop = FALSE]
            else ser2[ser2$station == st, , drop = FALSE]
    s_ref <- ref_ser[[as.character(which)]]
    if (!nrow(s_st) || !nrow(s_ref)) next
    res_lag <- lag_for_station(st, s_st, s_ref, MAX_LAG_H)
    if (is.null(res_lag)) next
    lag_rows[[length(lag_rows) + 1L]] <- data.frame(
      station = st, peak_type = which, method = "circular_phase",
      lag_h = res_lag$lag, r_at_lag = res_lag$r_concentration,
      n_pairs = res_lag$n, lag_xcorr_h = res_lag$lag_xcorr,
      r_xcorr = res_lag$r_xcorr, stringsAsFactors = FALSE)
  }
}
lag_xcorr <- do.call(rbind, lag_rows)
say2("so (tram, loai dinh) uoc luong duoc: ", nrow(lag_xcorr))
print(lag_xcorr[, c("station", "peak_type", "lag_h", "r_at_lag", "lag_xcorr_h",
                    "r_xcorr", "n_pairs")])

# ---------------------------------------------------------------------------
# 3. DO TRE THEO BO PHAN DINH (peak1 va peak2 rieng) + TRUNG VI / IQR / CI
# ---------------------------------------------------------------------------
# BOOTSTRAP THEO NGAY: resample cac NGAY (co the lap) cua chuoi chung, tinh
# lai do tre (trung vi von Mises) -> phan bo do tre -> trung vị, IQR,
# CI 95% percentile.
#
# LUU Y: resample theo ngay giu nguyen phu thuoc giua cac moc trong cung ngay
# (dinh 1 va dinh 2 cua cung ngay khong phai la hai mau doc lap).
#
# Do tre luon duoc tinh tren HIEU TRON VONG 24 gio (nhu o muc 2).
bootstrap_lag <- function(station, s_st, s_ref, max_lag = MAX_LAG_H, n_boot = N_BOOT,
                          seed = SEED) {
  common <- intersect(s_ref$day, s_st$day)
  if (length(common) < 60) return(NULL)
  set.seed(seed)
  boot <- numeric(n_boot)
  days <- common
  for (b in seq_len(n_boot)) {
    pick <- sample(days, length(days), replace = TRUE)
    a <- s_ref$t_h[match(pick, s_ref$day)]
    bb <- s_st$t_h[match(pick, s_st$day)]
    ok <- !is.na(a) & !is.na(bb)
    a <- a[ok]; bb <- bb[ok]
    if (length(a) < 30) { boot[b] <- NA_real_; next }
    d <- ((bb - a + 12) %% 24) - 12
    ph <- atan2(mean(sin(2 * pi * d / 24)), mean(cos(2 * pi * d / 24)))
    boot[b] <- ph * 24 / (2 * pi)
  }
  boot <- boot[!is.na(boot)]
  if (!length(boot)) return(NULL)
  list(median = stats::median(boot),
       q25 = stats::quantile(boot, 0.25, names = FALSE),
       q75 = stats::quantile(boot, 0.75, names = FALSE),
       ci_lo = stats::quantile(boot, 0.025, names = FALSE),
       ci_hi = stats::quantile(boot, 0.975, names = FALSE),
       n_boot = length(boot))
}

say2("\n== Trung vi / IQR / CI bootstrap do tre ==")
bl_rows <- list()
for (st in sort(unique(pk$station))) {
  if (st == REF_STATION) next
  for (which in 1:2) {
    s_st <- if (which == 1L) ser1[ser1$station == st, , drop = FALSE]
            else ser2[ser2$station == st, , drop = FALSE]
    s_ref <- ref_ser[[as.character(which)]]
    if (!nrow(s_st) || !nrow(s_ref)) next
    b <- bootstrap_lag(st, s_st, s_ref, MAX_LAG_H, N_BOOT, SEED + which)
    if (is.null(b)) next
    bl_rows[[length(bl_rows) + 1L]] <- data.frame(
      station = st, peak_type = which,
      lag_median_h = b$median, lag_q25_h = b$q25, lag_q75_h = b$q75,
      lag_iqr_h = b$q75 - b$q25, lag_ci_lo_h = b$ci_lo, lag_ci_hi_h = b$ci_hi,
      n_boot = b$n_boot, stringsAsFactors = FALSE)
  }
}
boot_lag <- do.call(rbind, bl_rows)
print(boot_lag)

# ---------------------------------------------------------------------------
# 4. DO TRE THEO PHA HARMONIC (phuong phap bo sung) -- so sanh 2 phuong phap
# ---------------------------------------------------------------------------
# Tu he so dieu hoa cua `harmonic.R`: pha cua thanh phan M2 tai moi tram.
# Do tre (gio) = (pha_M2[tram] - pha_M2[VUNG TAU]) / (360 / chu ky_M2)
# Quy uoc: pha lon hon => dinh xuat hien SOM hon.
#
# Dinh nghia pha trong `R/harmonics.R`: h(t) = a0 + SUM A_j cos(w_j t - g_j),
# nen g_j = atan2(b_j, a_j). Doi sang "tre" bang cach chia cho toc do goc.
comp_path <- file.path(OUT_E1, "harmonic_components.csv")
if (file.exists(comp_path)) {
  comp <- read_csv_df(comp_path)
  comp <- comp[comp$component == PHASE_COMPONENT, , drop = FALSE]
  if (nrow(comp)) {
    ph_ref <- comp$phase_deg[comp$station == REF_STATION]
    per_h <- HARMONIC_PERIOD_H[[PHASE_COMPONENT]]
    if (length(ph_ref) && is.finite(ph_ref)) {
      ph_rows <- list()
      for (i in seq_len(nrow(comp))) {
        if (comp$station[i] == REF_STATION) next
        dphase <- phase_diff_deg(comp$phase_deg[i], ph_ref)
        ph_rows[[length(ph_rows) + 1L]] <- data.frame(
          station = comp$station[i],
          phase_ref_deg = ph_ref, phase_station_deg = comp$phase_deg[i],
          phase_diff_deg = dphase,
          lag_h_harmonic = dphase / (360 / per_h),
          amp_m = comp$amplitude_m[i], stringsAsFactors = FALSE)
      }
      phase_lag_harm <- do.call(rbind, ph_rows)
      say2("\n== Do tre theo pha harmonic (", PHASE_COMPONENT, ") ==")
      print(phase_lag_harm)
    }
  }
} else {
  phase_lag_harm <- NULL
  say2("[CANH BAO] khong tim thay ", comp_path, " -> bo qua uoc luong theo pha harmonic")
}

# Bang so sanh 2 phuong phap
if (!is.null(phase_lag_harm) && !is.null(boot_lag)) {
  cmp <- merge(boot_lag, phase_lag_harm, by = "station")
  cmp <- cmp[cmp$peak_type == 1L, , drop = FALSE]
  cmp$diff_h <- cmp$lag_median_h - cmp$lag_h_harmonic
  write_csv_df(cmp, file.path(OUT_E1, "phase_lag_method_compare.csv"))
  say2("bang so sanh phuong phap: ", nrow(cmp), " dong")
}

# ---------------------------------------------------------------------------
# 5. BANG TONG HOP DO TRE TUNG TRAM (viet ra results/E1)
# ---------------------------------------------------------------------------
# Cot: do tre trung viet (bootstrap, gop ca 2 loai dinh), IQR, CI 95%,
# do tre theo pha harmonic, khoang cach doc, ty so bien do (H7).
lag_sum <- boot_lag
if (!is.null(phase_lag_harm)) {
  lag_sum <- merge(lag_sum, phase_lag_harm[, c("station", "lag_h_harmonic",
                                               "phase_diff_deg", "amp_m")],
                   by = "station", all.x = TRUE)
}
# `dist_tbl` co cot `code` -> chi lay cot can dung de tranh trung ten khi merge.
lag_sum <- merge(lag_sum,
                 dist_tbl[, c("code", "dist_to_root_km", "comid", "uparea_km2")],
                 by.x = "station", by.y = "code", all.x = TRUE)

# Ty so bien do tram / VUNG TAU (H7)
amp_ref <- NA_real_
if (file.exists(comp_path)) {
  ca <- read_csv_df(comp_path)
  ca2 <- ca[ca$component == PHASE_COMPONENT, , drop = FALSE]
  ar <- ca2$amplitude_m[ca2$station == REF_STATION]
  if (length(ar)) amp_ref <- ar[1]
}
if (is.finite(amp_ref)) {
  lag_sum$amp_M2_m <- lag_sum$amp_m
  lag_sum$amp_ratio_vs_vungtau <- lag_sum$amp_M2_m / amp_ref
}
lag_sum <- lag_sum[order(lag_sum$station, lag_sum$peak_type), , drop = FALSE]
write_csv_df(lag_sum, file.path(OUT_E1, "phase_lag_by_station.csv"))
say2("\nghi phase_lag_by_station.csv: ", nrow(lag_sum), " dong")

# ---------------------------------------------------------------------------
# 6. H5: HOI QUY DO TRE THEO KHOANG CACH DOC SONG
# ---------------------------------------------------------------------------
# H5: do tre pha tang voi khoang cach doc song tu bien (VUNG TAU).
# Mo hinh: lag_median_h ~ dist_to_root_km  (OLS, tuong quan Pearson + Spearman)
say2("\n== H5: do tre theo khoang cach doc song ==")
h5_df <- lag_sum[lag_sum$peak_type == 1L &
                  !is.na(lag_sum$lag_median_h) &
                  !is.na(lag_sum$dist_to_root_km), , drop = FALSE]
h5_rows <- list()
if (nrow(h5_df) >= 4) {
  m5 <- stats::lm(lag_median_h ~ dist_to_root_km, data = h5_df)
  ct5 <- stats::cor.test(h5_df$dist_to_root_km, h5_df$lag_median_h,
                         method = "pearson")
  cs5 <- suppressWarnings(stats::cor.test(h5_df$dist_to_root_km, h5_df$lag_median_h,
                                          method = "spearman"))
  h5_rows[[1]] <- data.frame(
    model = "lag_median_h ~ dist_to_root_km", n = nrow(h5_df),
    intercept = unname(coef(m5)[1]), slope = unname(coef(m5)[2]),
    slope_se = summary(m5)$coefficients[2, 2],
    slope_ci_lo = confint(m5)[2, 1], slope_ci_hi = confint(m5)[2, 2],
    r_squared = summary(m5)$r.squared,
    pearson_r = unname(ct5$estimate), pearson_p = ct5$p.value,
    spearman_rho = unname(cs5$estimate), spearman_p = cs5$p.value,
    r = NA_real_, p_value = NA_real_, stringsAsFactors = FALSE)
  say2(sprintf("  H5: n=%d  slope=%.4f h/km (CI95 %s..%s)  R2=%.4f  p(Pearson)=%.4g",
               nrow(h5_df), coef(m5)[2], confint(m5)[2, 1], confint(m5)[2, 2],
               summary(m5)$r.squared, ct5$p.value))
} else {
  say2("  H5: du lieu khong du (n = ", nrow(h5_df), ")")
}
if (!is.null(phase_lag_harm) && nrow(h5_df) >= 4) {
  hh <- phase_lag_harm[phase_lag_harm$station %in% h5_df$station, , drop = FALSE]
  hh <- merge(hh, dist_tbl, by.x = "station", by.y = "code")
  if (nrow(hh) >= 4) {
    m5h <- stats::lm(lag_h_harmonic ~ dist_to_root_km, data = hh)
    c5h <- stats::cor.test(hh$dist_to_root_km, hh$lag_h_harmonic, method = "pearson")
    h5_rows[[length(h5_rows) + 1L]] <- data.frame(
      model = "lag_h_harmonic ~ dist_to_root_km", n = nrow(hh),
      intercept = unname(coef(m5h)[1]), slope = unname(coef(m5h)[2]),
      slope_se = summary(m5h)$coefficients[2, 2],
      slope_ci_lo = confint(m5h)[2, 1], slope_ci_hi = confint(m5h)[2, 2],
      r_squared = summary(m5h)$r.squared,
      pearson_r = unname(c5h$estimate), pearson_p = c5h$p.value,
      spearman_rho = NA_real_, spearman_p = NA_real_,
      r = NA_real_, p_value = NA_real_, stringsAsFactors = FALSE)
    say2(sprintf("  H5 (phuong phap pha harmonic): n=%d slope=%.4f h/km R2=%.4f p=%.4g",
                 nrow(hh), coef(m5h)[2], summary(m5h)$r.squared, c5h$p.value))
  }
}
h5_out <- do.call(rbind, h5_rows)
write_csv_df(h5_out, file.path(OUT_E1, "h5_lag_vs_distance.csv"))

# ---------------------------------------------------------------------------
# 7. H6: DO TRE THEO MUA VA THEO LUU LUONG XA HO
# ---------------------------------------------------------------------------
# H6: `lag ~ Q_release + season`.
#   - `Q_release` = tong luu luong XA (tran + qua may) cua cac ho thuong nguon
#     lien quan trong ngay DO (theo `reservoir_daily`).
#   - `season`   = "Mua mua" (thang 5-10) / "Mua kho" (thang 11-4) -- mien Nam.
# Mo hinh: lm(lag_median_h ~ q_release + season)
say2("\n== H6: do tre theo mua va luu luong xa ho ==")

# Do tre theo tram (dung chung cho ca 2 loai dinh) -- lay trung viet cua 2 loai
agg_lag <- stats::aggregate(lag_median_h ~ station, data = boot_lag, FUN = mean)

# Xa ho: tong luu luong xa theo ngay (tran + qua may) cho CAC HO thuong nguon
# lien quan voi tram. Dung bang tra COMID -> ho (tu `merit_river_reaches`) de
# gan ho vao tram. Neu khong gan duoc, dung tong tat ca cac ho (ghi ro trong README).
res_day <- stats::aggregate(cbind(spillway_discharge_m3s, turbine_discharge_m3s) ~ day,
                            data = res, FUN = sum, na.rm = TRUE)
res_day$q_release <- res_day$spillway_discharge_m3s + res_day$turbine_discharge_m3s
res_day$day <- as.character(res_day$day)

# Goc phinh: gan ho -> tram qua cay MERIT (COMID cua tram va COMID cua ho).
# Tai day dung `merit_river_reaches` de tinh duong di tu COMID ho den COMID tram.
say2("  doc `merit_river_reaches` de gan ho chua ==")
mr <- mongo_agg("merit_river_reaches",
                paste0('[{"$project":{"_id":0,"COMID":1,"NextDownID":1,',
                       '"up1":1,"up2":1,"up3":1,"up4":1}}]'))
mr$COMID <- as.numeric(mr$COMID)
mr$NextDownID <- as.numeric(mr$NextDownID)
for (i in 1:4) mr[[paste0("up", i)]] <- as.numeric(mr[[paste0("up", i)]])
say2("  merit_river_reaches: ", nrow(mr), " dong")

# Do duong di thuong nguon (theo NextDownID) tu COMID ho den COMID tram.
path_from <- function(from_comid, to_comid, nextdown) {
  if (is.na(from_comid) || is.na(to_comid)) return(NA_integer_)
  if (from_comid == to_comid) return(0L)
  cur <- from_comid
  seen <- c()
  for (k in 1:4000) {
    if (cur %in% seen) return(NA_integer_)
    seen <- c(seen, cur)
    nxt <- nextdown[as.character(cur)]
    if (is.na(nxt)) return(NA_integer_)
    if (nxt == 0) return(NA_integer_)
    if (nxt == to_comid) return(k)
    cur <- nxt
  }
  NA_integer_
}
nextdown_map <- mr$NextDownID
names(nextdown_map) <- as.character(mr$COMID)

# COMID cua ho: tu `stations` (RES_* co `merit.COMID`).
res_st <- stations_tbl[grepl("^RES_", stations_tbl$code), , drop = FALSE]
res_comid <- stats::setNames(res_st$merit.COMID, res_st$code)
res_comid <- res_comid[!is.na(res_comid)]

# COMID cua tram trieu
st_comid <- stats::setNames(dist_tbl$comid, dist_tbl$code)
st_comid <- st_comid[!is.na(st_comid)]

# Voi moi (ho, tram): khoang cach do duong -> ho co o TREN (nguoc dong) tram khong?
res_rel <- list()
for (rc in names(res_comid)) {
  for (sc in names(st_comid)) {
    d <- path_from(res_comid[[rc]], st_comid[[sc]], nextdown_map)
    if (!is.na(d)) {
      res_rel[[length(res_rel) + 1L]] <- data.frame(
        reservoir = rc, station = sc, dist_steps = d, stringsAsFactors = FALSE)
    }
  }
}
res_rel <- do.call(rbind, res_rel)
say2("  quan he ho - tram: ", nrow(res_rel), " cap (ho o thuong nguon tram)")

# Lay tong xa ho theo (tram, ngay)
res$day <- as.character(res$day)
res$q_release <- res$spillway_discharge_m3s + res$turbine_discharge_m3s

h6_rows <- list()
if (nrow(res_rel)) {
  # voi moi tram: tap ho thuong nguon
  rel_by_st <- split(res_rel$reservoir, res_rel$station)
  # bang xa ho theo (reservoir, day) -> q_release
  res_q <- res[, c("reservoir", "day", "q_release")]
  res_q <- res_q[!is.na(res_q$q_release), , drop = FALSE]
  q_rows <- list()
  for (sc in names(rel_by_st)) {
    hs <- unique(rel_by_st[[sc]])
    sub <- res_q[res_q$reservoir %in% hs, , drop = FALSE]
    if (!nrow(sub)) next
    # tong xa cua cac ho thuong nguon theo ngay
    agg <- stats::aggregate(q_release ~ day, data = sub, FUN = sum)
    agg$station <- sc
    q_rows[[length(q_rows) + 1L]] <- agg[, c("station", "day", "q_release")]
  }
  q_all <- do.call(rbind, q_rows)
  q_all$day <- as.character(q_all$day)
  # DO TRE THEO TUNG NGAY (de hoi quy H6): dung chinh cac CHENH LECH PHA TRON
  # cua cap (tram, ngay) da tinh o muc 2 -> bo sung vao bang ghep.
  # `lag_for_station()` tra `diffs` = hieu gio (VUNG TAU - TRAM) theo ngay.
  lag_day_rows <- list()
  for (st in sort(unique(pk$station))) {
    if (st == REF_STATION) next
    for (which in 1:2) {
      s_st <- if (which == 1L) ser1[ser1$station == st, , drop = FALSE]
              else ser2[ser2$station == st, , drop = FALSE]
      s_ref <- ref_ser[[as.character(which)]]
      if (!nrow(s_st) || !nrow(s_ref)) next
      rl <- lag_for_station(st, s_st, s_ref, MAX_LAG_H)
      if (is.null(rl)) next
      common <- intersect(s_ref$day, s_st$day)
      a <- s_ref$t_h[match(common, s_ref$day)]
      b <- s_st$t_h[match(common, s_st$day)]
      ok <- !is.na(a) & !is.na(b)
      lag_day_rows[[length(lag_day_rows) + 1L]] <- data.frame(
        station = st, peak_type = which, day = common[ok],
        lag_day_h = rl$diffs, stringsAsFactors = FALSE)
    }
  }
  lag_day <- do.call(rbind, lag_day_rows)
  lag_day <- merge(lag_day, dist_tbl[, c("code", "dist_to_root_km")],
                   by.x = "station", by.y = "code", all.x = TRUE)
  lag_day$month <- as.integer(substr(lag_day$day, 6, 7))
  lag_day$season <- ifelse(lag_day$month %in% 5:10, "Mua mua", "Mua kho")
  h6_df <- merge(lag_day, q_all, by = c("station", "day"), all.x = TRUE)
  h6_df <- h6_df[!is.na(h6_df$q_release), , drop = FALSE]
  if (nrow(h6_df) >= 30) {
    m6 <- stats::lm(lag_day_h ~ q_release + season, data = h6_df)
    ct6 <- summary(m6)$coefficients
    h6_rows[[1]] <- data.frame(
      term = rownames(ct6), estimate = ct6[, 1], std_error = ct6[, 2],
      t_value = ct6[, 3], p_value = ct6[, 4],
      r_squared = summary(m6)$r.squared, n = nrow(h6_df),
      formula = "lag_day_h ~ q_release + season", stringsAsFactors = FALSE)
    say2(sprintf("  H6: n=%d R2=%.4f  coef q_release=%.6f (p=%.4g)",
                 nrow(h6_df), summary(m6)$r.squared, coef(m6)[["q_release"]],
                 ct6["q_release", 4]))
    # them mo hinh chi co q_release va chi co season de so sanh
    for (f in c("lag_day_h ~ q_release", "lag_day_h ~ season",
                "lag_day_h ~ q_release + season + dist_to_root_km")) {
      mm <- stats::lm(f, data = h6_df)
      ctm <- summary(mm)$coefficients
      h6_rows[[length(h6_rows) + 1L]] <- data.frame(
        term = rownames(ctm), estimate = ctm[, 1], std_error = ctm[, 2],
        t_value = ctm[, 3], p_value = ctm[, 4],
        r_squared = summary(mm)$r.squared, n = nrow(h6_df),
        formula = f, stringsAsFactors = FALSE)
    }
  } else {
    say2("  H6: du lieu khong du (n = ", nrow(h6_df), ")")
  }
} else {
  say2("  H6: khong xac dinh duoc quan he ho - tram")
}
h6_out <- do.call(rbind, h6_rows)
if (!is.null(h6_out)) write_csv_df(h6_out, file.path(OUT_E1, "h6_lag_vs_discharge.csv"))

# ---------------------------------------------------------------------------
# 8. H7: TY SO BIEN DO TRAM / VUNG TAU THEO KHOANG CACH
# ---------------------------------------------------------------------------
say2("\n== H7: ty so bien do theo khoang cach ==")
if (file.exists(comp_path)) {
  ca <- read_csv_df(comp_path)
  h7_rows <- list()
  for (cp in c("M2", "S2", "K1", "O1")) {
    cc <- ca[ca$component == cp, , drop = FALSE]
    if (!nrow(cc)) next
    ar <- cc$amplitude_m[cc$station == REF_STATION]
    if (!length(ar) || !is.finite(ar[1])) next
    cc$amp_ref_m <- ar[1]
    cc$amp_ratio <- cc$amplitude_m / ar[1]
    cc <- merge(cc, dist_tbl, by.x = "station", by.y = "code")
    cc$component <- cp
    h7_rows[[length(h7_rows) + 1L]] <- cc[, c("station", "component", "amplitude_m",
                                             "amp_ref_m", "amp_ratio",
                                             "dist_to_root_km", "n_obs")]
  }
  h7 <- do.call(rbind, h7_rows)
  write_csv_df(h7, file.path(OUT_E1, "h7_amplitude_ratio.csv"))
  say2("  ghi h7_amplitude_ratio.csv: ", nrow(h7), " dong")
  # hoi quy log(ti so) ~ log(khoang cach) cho M2
  h7m2 <- h7[h7$component == "M2" & !is.na(h7$dist_to_root_km) &
                h7$dist_to_root_km > 0 & h7$amp_ratio > 0, , drop = FALSE]
  if (nrow(h7m2) >= 4) {
    m7 <- stats::lm(log(amp_ratio) ~ log(dist_to_root_km), data = h7m2)
    c7 <- summary(m7)$coefficients
    say2(sprintf("  H7 (M2): log(ty so) ~ log(khoang cach): he so = %.4f (p=%.4g) R2=%.4f n=%d",
                 c7[2, 1], c7[2, 4], summary(m7)$r.squared, nrow(h7m2)))
    h7_out <- data.frame(
      component = "M2", model = "log(amp_ratio) ~ log(dist_to_root_km)",
      slope = c7[2, 1], slope_se = c7[2, 2], p_value = c7[2, 4],
      r_squared = summary(m7)$r.squared, n = nrow(h7m2),
      intercept = c7[1, 1], stringsAsFactors = FALSE)
    write_csv_df(h7_out, file.path(OUT_E1, "h7_amplitude_ratio_fit.csv"))
  }
} else {
  say2("  H7: khong co harmonic_components.csv")
}

# ---------------------------------------------------------------------------
# 9. TUONG QUAN CHEO XA HO <-> PHAN DU PHI THIEN VAN (do tre 0-5 ngay)
# ---------------------------------------------------------------------------
# Phan du phi thien van (thuc do - thien van) theo (tram, ngay) da duoc ghi
# trong `harmonic.R` (`results/E1/astro_residual.csv`).
# Tinh tuong quan CHEO giua chuoi xa ho (theo ngay) va chuoi phan du (theo tram,
# ngay) cho cac do tre 0..5 ngay, va tim do tre cho do tuong quan cao nhat.
say2("\n== Tuong quan cheo xa ho <-> phan du phi thien van ==")
resid_path <- file.path(OUT_E1, "astro_residual.csv")
if (!file.exists(resid_path)) {
  say2("  [CANH BAO] khong co ", resid_path)
} else {
  ar <- read_csv_df(resid_path)
  ar$day <- as.character(ar$day)
  # gom phan du theo (tram, ngay): trung binh cac dinh trong ngay
  ar_day <- stats::aggregate(residual_m ~ station + day, data = ar, FUN = mean)
  res_rel2 <- res_rel
  out_rows <- list()
  for (rc in unique(res$reservoir)) {
    # cac tram ma ho nay thuong nguon
    st_list <- if (!is.null(res_rel2)) unique(res_rel2$station[res_rel2$reservoir == rc]) else character(0)
    if (!length(st_list)) next
    # chuoi xa ho (theo ngay) cho ho nay
    hs <- res[res$reservoir == rc, , drop = FALSE]
    if (!nrow(hs)) next
    hq <- stats::aggregate(q_release ~ day, data = hs, FUN = sum, na.rm = TRUE)
    hq$day <- as.character(hq$day)
    for (sc in st_list) {
      sd_ <- ar_day[ar_day$station == sc, , drop = FALSE]
      if (!nrow(sd_)) next
      common <- intersect(hq$day, sd_$day)
      if (length(common) < 60) next
      qv <- hq$q_release[match(common, hq$day)]
      rv <- sd_$residual_m[match(common, sd_$day)]
      ok <- !is.na(qv) & !is.na(rv)
      qv <- qv[ok]; rv <- rv[ok]
      if (length(qv) < 60) next
      for (L in 0:5) {
        if (L == 0) {
          a <- qv; b <- rv
        } else {
          if (length(qv) <= L + 30) next
          a <- qv[seq_len(length(qv) - L)]
          b <- rv[(L + 1):length(rv)]
        }
        a <- a - mean(a); b <- b - mean(b)
        sa <- sqrt(sum(a * a)); sb <- sqrt(sum(b * b))
        if (!is.finite(sa) || !is.finite(sb) || sa == 0 || sb == 0) next
        out_rows[[length(out_rows) + 1L]] <- data.frame(
          reservoir = rc, station = sc, lag_days = L,
          r = sum(a * b) / (sa * sb), n = length(a), stringsAsFactors = FALSE)
      }
    }
  }
  cc_out <- do.call(rbind, out_rows)
  if (!is.null(cc_out)) {
    write_csv_df(cc_out, file.path(OUT_E1, "crosscorr_release_residual.csv"))
    say2("  ghi crosscorr_release_residual.csv: ", nrow(cc_out), " dong")
    # bang tom tat: do tre toi uu theo (ho, tram)
    best <- do.call(rbind, lapply(split(cc_out, paste(cc_out$reservoir, cc_out$station,
                                                     sep = "|")), function(d) {
      d[which.max(d$r), , drop = FALSE]
    }))
    write_csv_df(best, file.path(OUT_E1, "crosscorr_release_residual_best.csv"))
    print(best)
  }
}

# ---------------------------------------------------------------------------
# 10. HINH
# ---------------------------------------------------------------------------
say2("\n== Hinh ==")

# 10.1 Do tre theo tram (bootstrap)
if (nrow(boot_lag)) {
  p1 <- ggplot(boot_lag, aes(x = station, y = lag_median_h)) +
    geom_linerange(aes(ymin = lag_ci_lo_h, ymax = lag_ci_hi_h),
                   color = palette_pgnn1(1), linewidth = 0.6) +
    geom_point(aes(y = lag_median_h, color = factor(peak_type)), size = 2.4) +
    geom_hline(yintercept = 0, color = "#888888", linetype = "dashed", linewidth = 0.4) +
    scale_color_manual(values = palette_pgnn(2),
                       labels = c("1" = "Dinh chinh (peak 1)", "2" = "Dinh phu (peak 2)")) +
    labs(title = "Do tre pha trieu so voi Vung Tau",
         subtitle = "Tuong quan cheo gio dinh; khoang tin cay 95% bootstrap (2000 lan, theo ngay)",
         x = NULL, y = "Do tre (gio)", color = NULL) +
    theme_pgnn() + theme(axis.text.x = element_text(angle = 30, hjust = 1))
  save_fig(print(p1), file.path(OUT_E1, "phase_lag_by_station.png"),
           width = 1440, height = 800)
}

# 10.2 H5: do tre vs khoang cach doc
if (nrow(h5_df) >= 3) {
  p2 <- ggplot(h5_df, aes(x = dist_to_root_km, y = lag_median_h)) +
    geom_smooth(method = "lm", se = TRUE, color = palette_pgnn1(2), fill = "#D55E0022") +
    geom_point(aes(color = station), size = 2.6) +
    geom_hline(yintercept = 0, color = "#888888", linetype = "dashed", linewidth = 0.4) +
    scale_color_manual(values = palette_pgnn(nrow(h5_df))) +
    labs(title = "H5: do tre pha theo khoang cach doc song",
         subtitle = sprintf("OLS: lag = %.3f + %.4f * khoang cach km   (R2 = %.3f)",
                             if (nrow(h5_out)) h5_out$intercept[1] else NA,
                             if (nrow(h5_out)) h5_out$slope[1] else NA,
                             if (nrow(h5_out)) h5_out$r_squared[1] else NA),
         x = "Khoang cach doc song tu Vung Tau (km)", y = "Do tre trung vi (gio)",
         color = "Tram") +
    theme_pgnn()
  save_fig(print(p2), file.path(OUT_E1, "h5_lag_vs_distance.png"),
           width = 1280, height = 800)
}

# 10.3 H7: ty so bien do vs khoang cach
if (!is.null(h7) && nrow(h7)) {
  p3 <- ggplot(h7, aes(x = dist_to_root_km, y = amp_ratio, color = component)) +
    geom_hline(yintercept = 1, color = "#888888", linetype = "dashed", linewidth = 0.4) +
    geom_point(size = 2.6) +
    scale_color_manual(values = palette_pgnn(length(unique(h7$component)))) +
    labs(title = "H7: ty so bien do tram / Vung Tau theo khoang cach doc",
         subtitle = "Ty so = bien do thanh phan tai tram / bien do tai Vung Tau",
         x = "Khoang cach doc song (km)", y = "Ty so bien do", color = "Thanh phan") +
    theme_pgnn()
  save_fig(print(p3), file.path(OUT_E1, "h7_amplitude_ratio.png"),
           width = 1280, height = 800)
}

# 10.4 Do tre theo mua (mua mua / mua kho)
if (exists("lag_day") && !is.null(lag_day) && nrow(lag_day)) {
  ld2 <- lag_day
  p4 <- ggplot(ld2, aes(x = season, y = lag_day_h, fill = season)) +
    geom_boxplot(outlier.shape = NA, alpha = 0.6, width = 0.6) +
    geom_hline(yintercept = 0, color = "#888888", linetype = "dashed", linewidth = 0.4) +
    scale_fill_manual(values = palette_pgnn(2)) +
    labs(title = "H6: do tre pha theo mua",
         subtitle = "Mua mua = thang 5-10; mua kho = thang 11-4 (mien Nam)",
         x = NULL, y = "Do tre trung vi (gio)") +
    theme_pgnn()
  save_fig(print(p4), file.path(OUT_E1, "h6_lag_by_season.png"),
           width = 1120, height = 800)
}

# ---------------------------------------------------------------------------
# 11. TONG HOP
# ---------------------------------------------------------------------------
say2("\n================ KET QUA phase_lag.R ================")
say2("din thuc do: ", nrow(pk), " dong (tram x ngay)")
say2("tram co do tre (tru Vung Tau): ", length(unique(boot_lag$station)))
if (nrow(boot_lag)) {
  say2("do tre trung vi: min ", min(boot_lag$lag_median_h), " h; max ",
       max(boot_lag$lag_median_h), " h")
}
if (!is.null(h5_out)) {
  for (i in seq_len(nrow(h5_out))) {
    r <- h5_out[i, ]
    say2(sprintf("  H5 [%s]: n=%d slope=%.4f h/km R2=%.4f p=%.4g",
                 r$model, r$n, r$slope, r$r_squared, r$pearson_p))
  }
}
if (!is.null(h6_out)) say2("  H6: ", nrow(h6_out), " dong he so")
say2("=====================================================")
