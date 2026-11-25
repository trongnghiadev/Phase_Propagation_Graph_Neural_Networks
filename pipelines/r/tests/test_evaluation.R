#!/usr/bin/env Rscript
# tests/test_evaluation.R -- Unit test cho ham danh gia (Chuong 4, muc 4.5).
#
#   Rscript pipelines/r/tests/test_evaluation.R
#
# Chay bang `stopifnot` (khong can framework test). Ket qua: neu moi ham deu
# dung, script thoat voi ma 0 va in "[OK] ..." cho tung muc.
#
# DU LIEU GIA CO DAP AN BIET TRUOC: cac truong hop duoc THIET KE de co ket qua
# tinh toan bang tay (khong phai "so sanh voi ket qua cua chinh ham dang test").
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

source(file.path(repo, "pipelines", "r", "R", "connect.R"))
source(file.path(repo, "pipelines", "r", "R", "io.R"))
source(file.path(repo, "pipelines", "r", "R", "rules.R"))
source(file.path(repo, "pipelines", "r", "R", "harmonics.R"))
source(file.path(repo, "pipelines", "r", "R", "evaluation.R"))

use_repo_rlib(repo)

n_pass <- 0
n_fail <- 0
ok <- function(label, expr) {
  res <- tryCatch(isTRUE(expr), error = function(e) {
    cat("   !! loi: ", conditionMessage(e), "\n"); FALSE
  })
  if (res) {
    n_pass <<- n_pass + 1
    cat("[OK]  ", label, "\n")
  } else {
    n_fail <<- n_fail + 1
    cat("[FAIL]", label, "\n")
  }
}
near <- function(a, b, tol = 1e-8) isTRUE(all(abs(a - b) < tol))

cat("=== Unit test ham danh gia (pipelines/r/R/evaluation.R) ===\n")

# ---------------------------------------------------------------------------
# 1. Quy tac ngay cua dinh (rules.R) -- phai khop voi Spark
# ---------------------------------------------------------------------------
cat("\n-- 1. Quy tac ngay cua dinh --\n")
ok("peak_day_offset: cung ngay -> 0",
   peak_day_offset("2024-03-10", "2024-03-10") == 0L)
ok("peak_day_offset: ngay ke tiep -> 1",
   peak_day_offset("2024-03-10", "2024-03-11") == 1L)
ok("peak_day_offset: truoc ngay quan sat -> 0",
   peak_day_offset("2024-03-10", "2024-03-09") == 0L)
ok("day_offset vector hoat dong",
   identical(day_offset(c("2024-03-10", "2024-03-10"), c("2024-03-10", "2024-03-11")),
             c(0L, 1L)))

# ---------------------------------------------------------------------------
# 2. Quy doi thoi gian UTC -> Viet Nam
# ---------------------------------------------------------------------------
cat("\n-- 2. Quy doi thoi gian --\n")
t_utc <- as.POSIXct("2024-03-10T02:00:00", tz = "UTC", format = "%Y-%m-%dT%H:%M:%S")
# `to_vn` giu NGUYEN moc thoi gian (epoch) va chi doi cach HIEN THI:
# -> giá trị số phải bằng nhau, còn giờ hiển thị phải +7.
ok("to_vn giu nguyen moc thoi gian (epoch khong doi)",
   near(as.numeric(difftime(to_vn(t_utc), t_utc, units = "hours")), 0))
ok("to_vn hien thi +7 gio (02:00Z -> 09:00 VN)",
   format(to_vn(t_utc), "%Y-%m-%d %H:%M:%S", tz = PGNN_TZ) == "2024-03-10 09:00:00")
ok("to_vn cong 7 gio so voi gio UTC",
   near(as.numeric(as.POSIXlt(to_vn(t_utc), tz = PGNN_TZ)$hour) -
          as.numeric(as.POSIXlt(t_utc, tz = "UTC")$hour), 7))
ok("vn_day dung ngay Viet Nam", vn_day(t_utc) == "2024-03-10")
t_utc22 <- as.POSIXct("2024-03-10T20:00:00", tz = "UTC", format = "%Y-%m-%dT%H:%M:%S")
ok("vn_day qua nua dem (20:00Z -> 03:00 VN ngay hom sau)",
   vn_day(t_utc22) == "2024-03-11")
ok("parse_t_peak_utc -> POSIXct Viet Nam",
   format(parse_t_peak_utc("2024-03-10T02:00:00Z"), "%Y-%m-%d %H:%M:%S",
          tz = "Asia/Ho_Chi_Minh") == "2024-03-10 09:00:00")

# ---------------------------------------------------------------------------
# 3. Dieu hoa: khoi phuc bien do/pha biet truoc
# ---------------------------------------------------------------------------
cat("\n-- 3. Dieu hoa (khoi phuc bien do biet truoc) --\n")
set.seed(42)
P_M2 <- 12.4206012 * 3600
P_K1 <- 23.9344695 * 3600
tt <- seq(0, 200 * 86400, by = 1800)
yy <- 1.0 + 0.80 * cos(2 * pi * tt / P_M2 + 0.3) + 0.35 * cos(2 * pi * tt / P_K1 - 0.6)
fit <- fit_harmonics(tt, yy, components = c("M2", "K1"), trend = FALSE)
ok("fit bien do M0 khop ~0.80 (sai so < 0.01 m)",
   abs(fit$amplitude[["M2"]] - 0.80) < 0.01)
ok("fit bien do K1 khop ~0.35 (sai so < 0.01 m)",
   abs(fit$amplitude[["K1"]] - 0.35) < 0.01)
ok("fit he so trung binh khop ~1.00", abs(unname(fit$coef[["a0"]]) - 1.00) < 0.01)
ok("var_expl ~ 1", fit$var_expl > 0.999)
# Moc thoi gian KHONG deu: phuong phap phai van dung
idx <- sort(sample(seq_along(tt), 2000))
fit2 <- fit_harmonics(tt[idx], yy[idx], components = c("M2", "K1"), trend = FALSE)
ok("fit tren moc thoi gian khong deu: bien do M2 khop ~0.80 (sai so < 0.03 m)",
   abs(fit2$amplitude[["M2"]] - 0.80) < 0.03)
ok("fit tren moc thoi gian khong deu: var_expl ~ 1", fit2$var_expl > 0.999)

# ---------------------------------------------------------------------------
# 4. Ghep du bao - thuc do (±6 gio)
# ---------------------------------------------------------------------------
cat("\n-- 4. Ghep du bao - thuc do --\n")
# LUU Y: `day` trong bang thuc do la NGAY QUAN SAT cua ban tin -> trung
# nhat theo (station, day). Do duoi day la du bao -> MOI ngay co 1 ban ghi
# quan sat voi 2 dinh (peak_idx 0 = dinh 1, 1 = dinh 2).
obs <- data.frame(
  station = "PHU_AN",
  day = c("2024-03-10", "2024-03-11", "2024-03-12"),
  obs_h1 = c(1.40, 1.45, 1.50),
  obs_h2 = c(0.28, 0.31, 0.33),
  obs_t1_h = c(19.5, 20.0, 7.5),
  obs_t2_h = c(6.5, 7.0, 18.5),
  stringsAsFactors = FALSE)
pred <- data.frame(
  model = "TEST", station = "PHU_AN",
  issued_date = rep("2024-03-09", 3),
  target_date = c("2024-03-10", "2024-03-10", "2024-03-12"),
  lead_day = c(1L, 1L, 3L),
  peak_idx = c(0L, 1L, 0L),
  h_q10 = NA_real_, h_q50 = c(1.50, 0.20, 1.45), h_q90 = NA_real_,
  t_peak = c("2024-03-10T12:30:00Z", "2024-03-09T23:30:00Z", "2024-03-12T04:30:00Z"),
  stringsAsFactors = FALSE)
pred$t_peak_vn <- parse_t_peak_utc(pred$t_peak)
mt <- match_forecast_observed(pred, obs, window_h = 6)
ok("ca 3 ban ghi duoc ghep (khong co ban ghi nao vuot 6 gio)", nrow(mt) == 3)
ok("dinh 0 ngay 10/03: dung do cao 1.50 (sai so +0.10 m)",
   abs(mt$h_q50[mt$target_date == "2024-03-10" & mt$peak_idx == 0] - 1.50) < 1e-9)
ok("dinh 1 ngay 10/03: khop dung chan (obs 0.28, du bao 0.20 -> -0.08 m)",
   abs(mt$d_h[mt$target_date == "2024-03-10" & mt$peak_idx == 1] - (0.20 - 0.28)) < 1e-9)
ok("khong ghep ban ghi o bien > 6 gio",
   {p2 <- pred; p2$t_peak_vn[1] <- as.POSIXct("2024-03-11 12:00:00", tz = "Asia/Ho_Chi_Minh",
                                            format = "%Y-%m-%d %H:%M:%S")
    nrow(match_forecast_observed(p2, obs, window_h = 6)) == 2})
ok("doi chieu 24 gio: du bao lech 19.5 gio van duoc ghep (buoi sang <-> buoi chieu)",
   {p3 <- pred; p3$t_peak_vn[1] <- as.POSIXct("2024-03-10 00:00:00", tz = "Asia/Ho_Chi_Minh",
                                            format = "%Y-%m-%d %H:%M:%S")
    r <- match_forecast_observed(p3, obs, window_h = 6)
    nrow(r) == 3})

# ---------------------------------------------------------------------------
# 5. Chi so
# ---------------------------------------------------------------------------
cat("\n-- 5. Chi so --\n")
mt2 <- mt[order(mt$station, mt$target_date, mt$peak_idx), ]
cm <- compute_metrics(mt2)
# tinh tay: d_h = +0.10, -0.08, -0.05 -> MAE = (0.10+0.08+0.05)/3 = 0.076667
ok("MAE = 0.076667 (tinh tay)", near(cm$mae, (0.10 + 0.08 + 0.05) / 3, 1e-6))
ok("bias = -0.01 (tinh tay)", near(cm$bias, (0.10 - 0.08 - 0.05) / 3, 1e-6))
ok("RMSE = sqrt((0.01+0.0064+0.0025)/3) = 0.07937 (tinh tay)",
   near(cm$rmse, sqrt((0.10^2 + 0.08^2 + 0.05^2) / 3), 1e-6))
ok("n = 3", cm$n == 3)
cat("   [debug] d_h =", paste(round(mt2$d_h, 4), collapse = ", "),
    " | mae =", cm$mae, " bias =", cm$bias, "\n")

# NSE: voi du bao = thuc do + bias, NSE < 1
ok("NSE tinh san (khong NaN)", is.finite(cm$nse))
ok("KGE tinh san (khong NaN)", is.finite(cm$kge))

# ---------------------------------------------------------------------------
# 6. Chi so bao dong: POD / FAR / CSI (bang so tay)
# ---------------------------------------------------------------------------
cat("\n-- 6. Chi so bao dong (POD/FAR/CSI) --\n")
# bang ghep gia: 4 cap
mex <- data.frame(
  station = "S", target_date = c("d1", "d2", "d3", "d4"),
  model = "T", lead_day = 1L, peak_idx = 0L,
  h_q50 = c(1.5, 1.5, 0.5, 0.5),
  obs_h = c(1.5, 0.5, 1.5, 0.5),
  d_h = c(0, 1, -1, 0), d_t_h = c(0, 0, 0, 0),
  t_peak_vn = as.POSIXct(rep("2024-03-10 12:00:00", 4), tz = "Asia/Ho_Chi_Minh",
                        format = "%Y-%m-%d %H:%M:%S"),
  matched_by = "peak_idx", stringsAsFactors = FALSE)
# nguong bao dong I = 1.0 cho ca 4 ngay
thr_x <- list(); for (k in paste0("S|d", 1:4)) thr_x[[k]] <- list(I = 1.0, II = 2.0, III = 3.0)
lv_x <- c("S|d1" = "I", "S|d2" = "", "S|d3" = "I", "S|d4" = "")
cs <- contingency_scores(mex, lv_x, thresholds = thr_x, threshold = "I")
ok("POD = 1/2 = 0.5 (tinh tay)", near(cs$POD[cs$threshold == "I"], 0.5, 1e-9))
ok("FAR = 1/2 = 0.5 (tinh tay)", near(cs$FAR[cs$threshold == "I"], 0.5, 1e-9))
ok("CSI = 1/3 = 0.3333 (tinh tay)", near(cs$CSI[cs$threshold == "I"], 1 / 3, 1e-9))
ok("bang bao dong co 4 ngay", cs$n[cs$threshold == "I"] == 4)

# ---------------------------------------------------------------------------
# 7. Kiem dinh: Wilcoxon, Diebold-Mariano, Holm
# ---------------------------------------------------------------------------
cat("\n-- 7. Kiem dinh --\n")
# Hai mo hinh: A sai co he +0.10, B sai bang 0 -> Wilcoxon phai cho p < 0.05
d_a <- data.frame(station = "S", target_date = paste0("d", 1:30),
                  peak_idx = 0L, d_h = rep(0.10, 30))
d_b <- data.frame(station = "S", target_date = paste0("d", 1:30),
                  peak_idx = 0L, d_h = rep(0, 30))
w <- wilcoxon_paired(d_a, d_b)
ok("Wilcoxon: p-value < 0.05 (khac biet co y nghia)", w$p_value < 0.05)
ok("Wilcoxon: n_pairs = 30", w$n_pairs == 30)
# Hai mo hinh giong nhau -> p > 0.05
d_c <- d_b; d_c$d_h <- rep(0, 30)
w2 <- wilcoxon_paired(d_b, d_c)
ok("Wilcoxon: p-value > 0.05 khi hai mo hinh giong nhau", w2$p_value > 0.05)
# Diebold-Mariano: hieu sai so LOSS (mo hinh - chuan) co trung binh != 0 -> p < 0.05
# Dung chuoi sai so co bien dong (nhung co sai co he duong) - mo phong sai so
# du bao that: bien do ngau nhien + sai co he.
set.seed(11)
dm_d <- 0.10 + 0.02 * sin(seq(0, 2 * pi, length.out = 40))
dm <- dm_test(dm_d, loss = "abs")
ok("Diebold-Mariano: p-value < 0.05 khi sai so khac 0 khac 0", dm$p_value < 0.05)
ok("Diebold-Mariano: mean_loss ~ 0.10 (tinh tay)", abs(dm$mean_loss - mean(abs(dm_d))) < 1e-9)
dm0 <- dm_test(rep(0, 40), loss = "abs")
ok("Diebold-Mariano: p-value > 0.05 khi sai so trung binh = 0", dm0$p_value > 0.05)
ok("Diebold-Mariano: chuoi hang -> p = 1 (khong co bang chung)",
   dm_test(rep(0.10, 40), loss = "abs")$p_value == 1)
# Holm (step-down) tren p = (0.01, 0.04, 0.03):
#   xep theo: 0.01 -> 3*0.01 = 0.03 ; 0.03 -> 2*0.03 = 0.06 ; 0.04 -> 1*0.04 = 0.04
#   -> ap dung nguyên te cuc don dieu (running max): 0.03, 0.06, 0.06
#   (p'_3 = 0.06 chu khong phai 0.04, vi phai bao dam p'_3 >= p'_2)
hp <- holm_adjust(c(0.01, 0.04, 0.03))
ok("Holm: p-value khong giam (monotonic)", all(diff(hp) >= 0))
ok("Holm: 3 p-value -> p'_3 = 0.06 (max(1*0.04, 2*0.03))", near(hp[3], 0.06, 1e-9))
ok("Holm: p'_1 = 0.03 (3 * 0.01)", near(hp[1], 0.03, 1e-9))
ok("Holm: p'_2 = 0.06 (2 * 0.03)", near(hp[2], 0.06, 1e-9))
ok("Holm: p'_2 >= p'_1 va p'_3 >= p'_2 (monotonic theo thu tu goc)",
   hp[1] <= hp[2] && hp[2] <= hp[3])
ok("Holm: p'_3 >= p goc (khong giam)", all(hp >= c(0.01, 0.04, 0.03) - 1e-12))

# ---------------------------------------------------------------------------
# 8. evaluate_predictions: tach theo model / lead_day / peak_idx
# ---------------------------------------------------------------------------
cat("\n-- 8. evaluate_predictions --\n")
pa <- pred; pa$model <- "A"
pb <- pred; pb$model <- "B"
# B = A nghich dao: du bao lech nguoc dau -> |d_h| giong nhau -> MAE bang nhau
#   A: d_h = +0.10, -0.08, -0.05  ->  h_q50 = 1.50, 0.20, 1.45
#   B: d_h = -0.10, +0.08, +0.05  ->  h_q50 = 1.30, 0.36, 1.55
pb$h_q50 <- c(1.30, 0.36, 1.55)
pa$t_peak_vn <- parse_t_peak_utc(pa$t_peak)
pb$t_peak_vn <- parse_t_peak_utc(pb$t_peak)
ev <- evaluate_predictions(rbind(pa, pb), obs)
ok("evaluate: 2 mo hinh trong bang tong hop", nrow(ev$overall) == 2)
ok("evaluate: co bang theo lead_day", !is.null(ev$by_lead) && nrow(ev$by_lead) >= 2)
ok("evaluate: co bang theo peak_idx", !is.null(ev$by_peak) && nrow(ev$by_peak) >= 2)
ok("evaluate: co bang theo tram", !is.null(ev$by_station) && nrow(ev$by_station) >= 2)
# So sanh MAE cua 2 mo hinh tren cung bo cap ghep:
# A sai +0.05/+0.02/0.00 (MAE = 0.023333), B sai -0.05/-0.02/0.00 (MAE = 0.023333)
# -> HAI MO HINH "NGHICH DAO" phai cho MAE BANG NHAU.
ok("evaluate: MAE cua A = MAE cua B trong truong hop nghich dao", {
  a <- ev$overall[ev$overall$model == "A", "mae"]; b <- ev$overall[ev$overall$model == "B", "mae"]
  isTRUE(abs(a - b) < 1e-9)
})

# Kiem dinh giua 2 mo hinh: can TO NHI cap ghep chung (Wilcoxon >= 5, DM >= 20).
# Tao them 30 cap ghep gia cho 2 mo hinh de kiem dinh co y nghia.
set.seed(7)
n_extra <- 30
obs_big <- rbind(obs, data.frame(
  station = "PHU_AN",
  day = sprintf("2024-04-%02d", seq_len(n_extra)),
  obs_h1 = 1.40, obs_h2 = 0.30,
  obs_t1_h = 19.5, obs_t2_h = 6.5,
  stringsAsFactors = FALSE))
big <- data.frame(
  model = c(rep("A", n_extra), rep("B", n_extra)),
  station = "PHU_AN",
  issued_date = "2024-04-01",
  target_date = rep(sprintf("2024-04-%02d", seq_len(n_extra)), 2),
  lead_day = 1L, peak_idx = 0L,
  h_q10 = NA_real_, h_q90 = NA_real_,
  # A luon lech +0.15 m, B luon lech -0.05 m -> |e_A| - |e_B| = +0.10 > 0 (A kem hon)
  h_q50 = c(rep(1.55, n_extra), rep(1.35, n_extra)),
  t_peak = rep("2024-04-01T12:30:00Z", 2 * n_extra),
  stringsAsFactors = FALSE)
big$t_peak_vn <- parse_t_peak_utc(big$t_peak)
ev_big <- evaluate_predictions(rbind(pa, pb, big), obs_big)
ok("evaluate: co kiem dinh Wilcoxon giua 2 mo hinh",
   !is.null(ev_big$tests$wilcoxon) && nrow(ev_big$tests$wilcoxon) == 1)
ok("evaluate: co kiem dinh Diebold-Mariano",
   !is.null(ev_big$tests$dm) && nrow(ev_big$tests$dm) == 1)
ok("evaluate: Wilcoxon cho p < 0.05 (2 mo hinh sai co khac nhau)",
   !is.null(ev_big$tests$wilcoxon) && ev_big$tests$wilcoxon$p_value[1] < 0.05)
ok("evaluate: Diebold-Mariano cho p < 0.05",
   !is.null(ev_big$tests$dm) && ev_big$tests$dm$p_value[1] < 0.05)
ok("evaluate: co cot p_holm sau hieu chinh",
   !is.null(ev_big$tests$wilcoxon) && "p_holm" %in% names(ev_big$tests$wilcoxon))

# ---------------------------------------------------------------------------
# 9. read_predictions: chuan hoa du lieu dau vao
# ---------------------------------------------------------------------------
cat("\n-- 9. read_predictions --\n")
tmp_csv <- file.path(tempdir(), "pred_test.csv")
utils::write.csv(pred[, c("model", "station", "issued_date", "target_date",
                          "lead_day", "peak_idx", "h_q10", "h_q50", "h_q90", "t_peak")],
                 tmp_csv, row.names = FALSE)
rp <- read_predictions(tmp_csv)
ok("read_predictions: doc duoc 3 dong", nrow(rp) == 3)
ok("read_predictions: lead_day la integer", is.integer(rp$lead_day))
ok("read_predictions: h_q50 la numeric", is.numeric(rp$h_q50))
ok("read_predictions: t_peak_vn doi sang gio Viet Nam",
   format(rp$t_peak_vn[1], "%Y-%m-%d %H:%M:%S", tz = "Asia/Ho_Chi_Minh") == "2024-03-10 19:30:00")

# ---------------------------------------------------------------------------
# 10. PHA HARMONIC: WRAP / UNWRAP (sua loi GĐ4 - Bug 1)
# ---------------------------------------------------------------------------
# BANG CHUNG GOC: DAU_TIENG vs VUNG_TAU
#   phase_VUNG_TAU = -76.7521267189486 deg
#   phase_DAU_TIENG = +135.329623164239 deg
#   raw diff = +212.082 deg  ->  wrap = -147.918 deg
#   lag_h_harmonic (wrap)     = -5.1034 h   <-- SAI DAU
#   lag_h_harmonic (no wrap)  = +7.3172 h   <-- DUNG
cat("\n-- 10. PHA HARMONIC: wrap / unwrap --\n")
ph_vt <- -76.7521267189486
ph_dt <- 135.329623164239
per_m2 <- 12.4206012

ok("phase_diff_deg: wrap 212.082 deg -> -147.918 deg (gia tri goc cua loi)",
   near(phase_diff_deg(ph_dt, ph_vt),
        ((ph_dt - ph_vt + 180) %% 360) - 180, 1e-9) &&
   near(phase_diff_deg(ph_dt, ph_vt), -147.918250116812, 1e-6))
ok("phase_diff_unwrapped: 212.082 deg giu nguyen (KHONG wrap)",
   near(phase_diff_unwrapped(ph_dt, ph_vt), ph_dt - ph_vt, 1e-9) &&
   near(phase_diff_unwrapped(ph_dt, ph_vt), 212.081749883188, 1e-6))
ok("phase_diff_deg: giu nguyen khi |diff| < 180 deg",
   near(phase_diff_deg(105.0, -36.9252141469426), 141.9252141469426, 1e-9))
ok("phase_diff_unwrapped: giu nguyen khi |diff| < 180 deg",
   near(phase_diff_unwrapped(105.0, -36.9252141469426), 141.9252141469426, 1e-9))
# Ham goc KHONG bi sua (chi dung de so sanh pha don le)
ok("phase_diff_deg: wrap ve khoang (-180, 180]",
   phase_diff_deg(179, -179) == -2 && phase_diff_deg(-179, 179) == 2)

ok("phase_lag_hours (wrap = FALSE): DAU_TIENG = +7.3172 h",
   near(phase_lag_hours(ph_dt, ph_vt, per_m2, wrap = FALSE), 7.31715, 1e-4))
ok("phase_lag_hours (wrap = TRUE): DAU_TIENG = -5.1034 h (gia tri SAI cua loi)",
   near(phase_lag_hours(ph_dt, ph_vt, per_m2, wrap = TRUE), -5.10343, 1e-4))
ok("phase_lag_hours: mac dinh KHONG wrap",
   near(phase_lag_hours(ph_dt, ph_vt, per_m2), 7.31715, 1e-4))

# ---------------------------------------------------------------------------
# 11. QUY UOC DAU DO TRE (sua loi GĐ4 - Bug 4)
# ---------------------------------------------------------------------------
# KY HIEU CHUAN: lag > 0 => dinh tai TRAM xuat hien CHAM HON dinh tai BIEN.
cat("\n-- 11. Quy uoc dau do tre --\n")
# VUNG_TAU dinh luc 6:00; tram thuong nguon dinh luc 10:00 -> cham 4 gio
ok("phase_lag_hours: tram cham hon => lag DUONG",
   phase_lag_hours(10, 6, per_m2, wrap = FALSE) > 0)
ok("phase_lag_hours: tram som hon => lag AM",
   phase_lag_hours(6, 10, per_m2, wrap = FALSE) < 0)
ok("phase_lag_hours: dong pha => lag = 0",
   near(phase_lag_hours(30, 30, per_m2, wrap = FALSE), 0, 1e-9))
# Do tre 4 gio = 4/(360/12.4206) = 0.13800 rad = 120 deg
ok("phase_lag_hours: tre 4 gio -> pha 120 deg",
   near(phase_lag_hours(120, 0, per_m2, wrap = FALSE),
        120 / (360 / per_m2), 1e-9))
ok("phase_lag_hours: doi dau -> doi dau do tre",
   near(phase_lag_hours(0, 120, per_m2, wrap = FALSE),
        -phase_lag_hours(120, 0, per_m2, wrap = FALSE), 1e-9))
# Quy uoc hieu gio ngay (d = b - a voi b = TRAM, a = BIEN): duong = cham hon
ok("Quy uoc hieu gio ngay: (TRAM - BIEN) duong => tram cham hon",
   ((10 - 6 + 12) %% 24) - 12 > 0)
ok("Quy uoc hieu gio ngay: (BIEN - TRAM) se la dau nguoc",
   ((6 - 10 + 12) %% 24) - 12 < 0)

# ---------------------------------------------------------------------------
# 12. GAN NHOM DINH THEO CHU KY TRIEU (sua loi GĐ4 - Bug 2)
# ---------------------------------------------------------------------------
cat("\n-- 12. Gan nhom dinh theo chu ky trieu --\n")
# API that (R/rules.R):
#   assign_peak_generation(tide, phase_ref, period_h, n_per_gen, ref_tide, lag0)
#   - co `ref_tide`: t0 = dinh tham chieu DAU TIEN + lag0,
#     the he = floor((t - t0) / (2 * period_h))  (2 chu ky M2 ~ 24.84 h);
#   - TRONG mot the he, dinh thu N = dinh thu N theo THOI GIAN (khong theo gia
#     tri muc nuoc), giu toi da `n_per_gen` dinh cho MOI tram.
#   phase_lag.R goi qua peak_series_by_generation(..., lag0 = 0 mac dinh).
#
# Du lieu gia: dinh cach deu dung 1 chu ky M2 (trieu that troi ~50 phut/ngay,
# khong "reset" moi ngay). Dinh chinh (k chan) = 2.0 m, dinh phu (k le) = 1.0 m.
# Dinh DAU TIEN cua moi chuoi som 0.1 h -> cac dinh con lai khong nam dung bien
# the he (tranh sai so dau phay dong khi (t - t0) / (2P) ra so nguyen).
VN_TZ <- "Asia/Ho_Chi_Minh"
mk_tide_m2 <- function(station, base, lag_h = 0, n_peak = 8L,
                       values = rep(c(2.0, 1.0), length.out = n_peak),
                       first_shift_h = 0.1, period_h = per_m2) {
  k <- seq_len(n_peak) - 1L
  off_h <- k * period_h + lag_h
  off_h[1] <- off_h[1] - first_shift_h
  ts <- base + off_h * 3600
  lt <- as.POSIXlt(ts, tz = VN_TZ)
  data.frame(station = station, kind = "high", ts = ts,
             ts_hour = lt$hour + lt$min / 60 + lt$sec / 3600,
             value = values, obs_day = format(ts, "%Y-%m-%d", tz = VN_TZ),
             day_offset = 0L, stringsAsFactors = FALSE)
}
add_rn <- function(d) {
  d <- d[order(d$station, d$obs_day, d$ts), , drop = FALSE]
  d$rn <- ave(seq_len(nrow(d)), list(d$station, d$kind, d$obs_day), FUN = seq_along)
  rownames(d) <- NULL
  d
}
circ_lag <- function(m) ((m$t_h - m$t_ref_h + 12) %% 24) - 12
# Hai dinh cung the he: (dinh som, dinh muon) + khoang cach (gio)
gen_pairs <- function(g) {
  g1 <- g[g$gen_rn == 1, c("tide_gen", "ts", "value")]
  g2 <- g[g$gen_rn == 2, c("tide_gen", "ts", "value")]
  m <- merge(g1, g2, by = "tide_gen", suffixes = c("_1", "_2"))
  m$dt_h <- as.numeric(difftime(m$ts_2, m$ts_1, units = "hours"))
  m
}
# Khoang cach ky vong: 1 chu ky M2; rieng the he dau dai hon 0.1 h vi dinh
# dau tien cua chuoi duoc day som 0.1 h (xem `first_shift_h`).
exp_dt <- function(m) per_m2 + ifelse(m$tide_gen == min(m$tide_gen), 0.1, 0)

# --- 12a. Truong hop thuong: STATION_A tre 2 h, khong co dinh qua nua dem ---
LAG_A <- 2
base_a <- as.POSIXct("2024-01-01 02:00:00", tz = VN_TZ)
vt_a <- mk_tide_m2("VUNG_TAU", base_a)
st_a <- mk_tide_m2("STATION_A", base_a, lag_h = LAG_A)
tide_a <- add_rn(rbind(vt_a, st_a))

# Quy tac CU (pivot_extremes, khop Spark) giu nguyen: dinh 1 = gio som nhat trong ngay
fake_pk <- pivot_extremes(tide_a, "peak")
ok("pivot_extremes: giu quy tac thu tu gio trong ngay quan sat (R-Spark parity)",
   all(is.na(fake_pk$t2_h) | fake_pk$t1_h <= fake_pk$t2_h))

gen <- assign_peak_generation(st_a, phase_ref = 180, period_h = per_m2,
                              n_per_gen = 2L, ref_tide = vt_a, lag0 = 0)
ok("assign_peak_generation: moi the he co <= 2 dinh",
   all(table(gen$tide_gen) <= 2))
ok("assign_peak_generation: 8 dinh -> 4 the he, moi the he du 2 dinh",
   length(unique(gen$tide_gen)) == 4 && all(table(gen$tide_gen) == 2))
pa <- gen_pairs(gen)
ok("assign_peak_generation: trong the he, dinh 1 SOM hon dinh 2 (xep theo thoi gian)",
   nrow(pa) == 4 && all(pa$dt_h > 0))
ok("assign_peak_generation: 2 dinh cung the he cach nhau ~1 chu ky M2 (12.42 h)",
   nrow(pa) == 4 && all(abs(pa$dt_h - exp_dt(pa)) < 0.01))
gen_ref <- assign_peak_generation(vt_a, phase_ref = 180, period_h = per_m2,
                                  n_per_gen = 2L, ref_tide = vt_a, lag0 = 0)
ok("assign_peak_generation: tram va tram tham chieu co CUNG tap the he",
   identical(sort(unique(gen$tide_gen)), sort(unique(gen_ref$tide_gen))))
# Nhieu tram trong cung bang: moi tram duoc gioi han n_per_gen RIENG
gen_both <- assign_peak_generation(tide_a, phase_ref = 180, period_h = per_m2,
                                   n_per_gen = 2L, ref_tide = vt_a, lag0 = 0)
ok("assign_peak_generation: gom theo tung tram (2 tram x 8 dinh -> giu du 16)",
   nrow(gen_both) == 16 &&
   all(table(gen_both$station, gen_both$tide_gen) == 2))

gs_a <- peak_series_by_generation(tide_a, "STATION_A", vt_a, phase_ref = 180,
                                  period_h = per_m2, n_per_gen = 2L)
ok("peak_series_by_generation: tra ve 2 chuoi (peak1, peak2), moi chuoi 4 the he",
   length(gs_a) == 2 && !is.null(gs_a[[1]]) && !is.null(gs_a[[2]]) &&
   nrow(gs_a[[1]]) == 4 && nrow(gs_a[[2]]) == 4)
ok("peak_series_by_generation: do tre = +2 h cho ca dinh 1 va dinh 2",
   near(circ_lag(gs_a[[1]]), LAG_A, 1e-6) && near(circ_lag(gs_a[[2]]), LAG_A, 1e-6))
mc_a <- match_cycle_peaks(gs_a[[1]], lag_sign = 1)
ok("match_cycle_peaks: giu du 4 cap, d_h = +2 h",
   nrow(mc_a) == 4 && near(mc_a$d_h, LAG_A, 1e-6))

# --- 12b. Dinh "roi" qua nua dem + bien do bien doi -----------------------
# STATION_B tre 3 h. Chon moc sao cho dinh k = 2 cua tram roi vao 23:30 ngay
# 2024-01-01, dinh cung the he (k = 3) vao 11:55 ngay 2024-01-02. Dinh k = 3
# CAO hon dinh k = 2 (2.5 m vs 1.8 m) -> neu xep theo gia tri se dao thu tu.
LAG_B <- 3
target_b <- as.POSIXct("2024-01-01 23:30:00", tz = VN_TZ)
base_b <- target_b - (2 * per_m2 + LAG_B) * 3600
val_b <- rep(c(2.0, 1.0), length.out = 8L); val_b[3] <- 1.8; val_b[4] <- 2.5
vt_b <- mk_tide_m2("VUNG_TAU", base_b)
st_b <- mk_tide_m2("STATION_B", base_b, lag_h = LAG_B, values = val_b)
tide_b <- add_rn(rbind(vt_b, st_b))
i_late <- which(abs(st_b$ts_hour - 23.5) < 1e-6)
ok("(du lieu gia) dinh k=2 cua STATION_B luc 23:30 ngay 2024-01-01",
   length(i_late) == 1 && st_b$obs_day[i_late] == "2024-01-01" &&
   st_b$obs_day[i_late + 1L] == "2024-01-02")

gen_b <- assign_peak_generation(st_b, phase_ref = 180, period_h = per_m2,
                                n_per_gen = 2L, ref_tide = vt_b, lag0 = 0)
same_ts <- function(a, b) abs(as.numeric(difftime(a, b, units = "secs"))) < 1
g_late <- gen_b[same_ts(gen_b$ts, st_b$ts[i_late]), ]
g_next <- gen_b[same_ts(gen_b$ts, st_b$ts[i_late + 1L]), ]
ok("Quy tac MOI: dinh 23:30 va dinh 11:55 hom sau CUNG the he",
   nrow(g_late) == 1 && nrow(g_next) == 1 && g_late$tide_gen == g_next$tide_gen)
ok("Quy tac MOI: dinh 23:30 la dinh 1 (som hon) du thap hon dinh 2 (1.8 < 2.5 m)",
   nrow(g_late) == 1 && nrow(g_next) == 1 &&
   g_late$gen_rn == 1 && g_next$gen_rn == 2 && g_late$value < g_next$value)
pb <- gen_pairs(gen_b)
ok("Quy tac MOI: moi the he <= 2 dinh, 2 dinh cach nhau ~1 chu ky M2",
   all(table(gen_b$tide_gen) <= 2) &&
   nrow(pb) == 4 && all(abs(pb$dt_h - exp_dt(pb)) < 0.01))

# Quy tac CU (theo ngay quan sat) ghep dinh 11:05 va 23:30 cua ngay 01-01:
# hai dinh nay thuoc HAI the he khac nhau -> sai chu ky.
pk_b <- pivot_extremes(tide_b[tide_b$station == "STATION_B", ], "peak")
d01 <- pk_b[pk_b$day == "2024-01-01", ]
gen_of_hour <- function(h) gen_b$tide_gen[gen_b$obs_day == "2024-01-01" &
                                           abs(gen_b$ts_hour - h) < 1e-6]
ok("(doi chung) Quy tac CU: ngay 01-01 ghep 2 dinh thuoc 2 the he KHAC nhau",
   nrow(d01) == 1 && abs(d01$t2_h - 23.5) < 1e-6 &&
   length(gen_of_hour(d01$t1_h)) == 1 && length(gen_of_hour(d01$t2_h)) == 1 &&
   gen_of_hour(d01$t1_h) != gen_of_hour(d01$t2_h))

gs_b <- peak_series_by_generation(tide_b, "STATION_B", vt_b, phase_ref = 180,
                                  period_h = per_m2, n_per_gen = 2L)
ok("peak_series_by_generation: dinh qua nua dem van cho do tre = +3 h (ca 2 chuoi)",
   !is.null(gs_b[[1]]) && !is.null(gs_b[[2]]) &&
   nrow(gs_b[[1]]) == 4 && nrow(gs_b[[2]]) == 4 &&
   near(circ_lag(gs_b[[1]]), LAG_B, 1e-6) && near(circ_lag(gs_b[[2]]), LAG_B, 1e-6))
mb_ <- match_cycle_peaks(gs_b[[1]], lag_sign = 1, lag0 = LAG_B)
ok("match_cycle_peaks: dinh qua nua dem -> d_h = +3 h, khong cap nao bi am",
   nrow(mb_) == 4 && near(mb_$d_h, LAG_B, 1e-6))

# ---------------------------------------------------------------------------
# 13. KHOA GHEP CAP KIEM DINH: cung issued_date + lead_day (sua loi 24/11/2026)
# ---------------------------------------------------------------------------
cat("\n-- 13. Khoa ghep cap kiem dinh --\n")
# 2 mo hinh, 1 ngay dich, 2 chan troi. A: lead 1 sai 0.00, lead 3 sai 0.30.
# B: lead 1 sai 0.10, lead 3 sai 0.20. Thu tu dong cua B dao nguoc.
mk_m <- function(model, leads, errs) {
  data.frame(model = model, station = "S", target_date = "2024-08-10",
             issued_date = c("2024-08-10", "2024-08-08")[match(leads, c(1L, 3L))],
             lead_day = leads, peak_idx = 0L, d_h = errs, stringsAsFactors = FALSE)
}
ma <- mk_m("A", c(1L, 3L), c(0.00, 0.30))
mb <- mk_m("B", c(3L, 1L), c(0.20, 0.10))
pe <- paired_errors(ma, mb)
ok("paired_errors: 2 cap (moi chan troi 1 cap)", nrow(pe) == 2)
ok("paired_errors: lead 1 cua A ghep voi lead 1 cua B (0.00 vs 0.10)",
   near(pe$e_a[grepl("\\|1$", pe$key)], 0.00) && near(pe$e_b[grepl("\\|1$", pe$key)], 0.10))
ok("paired_errors: lead 3 cua A ghep voi lead 3 cua B (0.30 vs 0.20)",
   near(pe$e_a[grepl("\\|3$", pe$key)], 0.30) && near(pe$e_b[grepl("\\|3$", pe$key)], 0.20))
# Khoa cu (station|target|peak) ghep lead 1 cua A voi dong dau cua B (lead 3):
k_old_a <- paste(ma$station, ma$target_date, ma$peak_idx, sep = "|")
k_old_b <- paste(mb$station, mb$target_date, mb$peak_idx, sep = "|")
ok("(doi chung) khoa cu ghep SAI chan troi: lead 1 cua A voi lead 3 cua B",
   mb$lead_day[match(k_old_a[1], k_old_b)] == 3L)
# Cung ngay dich, cung lead nhung KHAC ngay phat -> khong ghep
mc <- mb; mc$issued_date <- "2024-08-01"
ok("paired_errors: khac issued_date -> khong ghep", nrow(paired_errors(ma, mc)) == 0)
# Mo hinh khong phu thuoc chan troi (B_harm): ghep voi MOI chan troi cua mo hinh kia
mh <- data.frame(model = "H", station = "S", target_date = "2024-08-10",
                 issued_date = "2024-08-10", lead_day = 1L, peak_idx = 0L, d_h = -0.05)
ph <- paired_errors(ma, mh, free1 = FALSE, free2 = TRUE)
ok("horizon_free: B_harm ghep voi ca lead 1 va lead 3 cua A",
   nrow(ph) == 2 && all(near(ph$e_b, -0.05)))
ph2 <- paired_errors(mh, ma, free1 = TRUE, free2 = FALSE)
ok("horizon_free: doi thu tu -> e_a la B_harm, e_b la A",
   nrow(ph2) == 2 && near(ph2$e_a, c(-0.05, -0.05)) && near(sort(ph2$e_b), c(0, 0.30)))
ok("horizon_free: khong khai bao -> B_harm (lead 1) chi ghep lead 1 cua A",
   nrow(paired_errors(ma, mh)) == 1)

# Wilcoxon/DM so do CHINH XAC (|e|), khong phai hieu sai so co dau
n30 <- 30
mk_e <- function(e) data.frame(station = "S", target_date = sprintf("2024-08-%02d", 1:n30),
                               peak_idx = 0L, issued_date = sprintf("2024-08-%02d", 1:n30),
                               lead_day = 1L, d_h = e)
w_sym <- wilcoxon_paired(mk_e(rep(0.15, n30)), mk_e(rep(-0.15, n30)))
ok("Wilcoxon: lech +0.15 vs -0.15 (|e| bang nhau) -> p = 1", w_sym$p_value == 1)
set.seed(3)
ea <- rnorm(200, 0, 0.2); eb <- -ea
ok("DM: e_b = -e_a (cung do chinh xac) -> mean_loss = 0, p = 1",
   {r <- dm_test(ea, eb, loss = "abs"); near(r$mean_loss, 0) && r$p_value == 1})
ok("DM: |e_a| > |e_b| co he thong -> mean_loss > 0, p < 0.05",
   {r <- dm_test(2 * eb + sign(eb) * 0.01, eb, loss = "abs"); r$mean_loss > 0 && r$p_value < 0.05})
ok("DM: chuoi chenh lech loss truyen thang (e_b = NULL) khong bi lay tri tuyet doi",
   near(dm_test(rep(c(-0.1, 0.1), 10))$mean_loss, 0))

# evaluate_predictions: kiem dinh tach theo chan troi; B_harm horizon_free
obs_h <- data.frame(station = "PHU_AN", day = sprintf("2024-08-%02d", 1:n30),
                    obs_h1 = 1.40, obs_h2 = 0.30, obs_t1_h = 19.5, obs_t2_h = 6.5)
pk <- do.call(rbind, lapply(1:2, function(ld) data.frame(
  model = "K", station = "PHU_AN",
  issued_date = format(as.Date(sprintf("2024-08-%02d", 1:n30)) - (ld - 1), "%Y-%m-%d"),
  target_date = sprintf("2024-08-%02d", 1:n30), lead_day = ld, peak_idx = 0L,
  h_q10 = NA_real_, h_q50 = 1.40 + ifelse(ld == 1, 0.02, 0.30), h_q90 = NA_real_,
  t_peak = sprintf("2024-08-%02dT12:30:00Z", 1:n30))))
ph_ <- data.frame(model = "B_harm", station = "PHU_AN",
                  issued_date = sprintf("2024-08-%02d", 1:n30),
                  target_date = sprintf("2024-08-%02d", 1:n30), lead_day = 1L, peak_idx = 0L,
                  h_q10 = NA_real_, h_q50 = 1.50, h_q90 = NA_real_,
                  t_peak = sprintf("2024-08-%02dT12:30:00Z", 1:n30))
pp <- rbind(pk, ph_); pp$t_peak_vn <- parse_t_peak_utc(pp$t_peak)
ev_h <- evaluate_predictions(pp, obs_h, horizon_free = "B_harm")
ok("evaluate: bang ghep co cot issued_date", "issued_date" %in% names(ev_h$matched))
ok("evaluate: horizon_free -> Wilcoxon B_harm vs K tren 2 x 30 cap (moi lead)",
   !is.null(ev_h$tests$wilcoxon) && ev_h$tests$wilcoxon$n_pairs[1] == 2 * n30)
ev_h0 <- evaluate_predictions(pp, obs_h)
ok("evaluate: khong horizon_free -> chi ghep lead 1 (30 cap)",
   !is.null(ev_h0$tests$wilcoxon) && ev_h0$tests$wilcoxon$n_pairs[1] == n30)

# ---------------------------------------------------------------------------
# 14. CHONG RO RI: giai doan fit dieu hoa khong cham tap test (sua loi 24/11/2026)
# ---------------------------------------------------------------------------
cat("\n-- 14. Giai doan fit dieu hoa --\n")
cfg <- load_splits()
ok("load_splits: doc config/splits.json (train/val/test)",
   all(c("train", "val", "test") %in% names(cfg$splits)) && nzchar(cfg$test_from))
ok("load_splits: harmonic_fit_splits khong chua test", !("test" %in% cfg$harmonic_fit_splits))
fw <- harmonic_fit_window(cfg)
ok("harmonic_fit_window: ket thuc truoc mo'c test", fw[2] < cfg$test_from)
cfg_bad <- cfg; cfg_bad$harmonic_fit_splits <- c("train", "test")
ok("harmonic_fit_window: fit chua tap test -> loi",
   inherits(tryCatch(harmonic_fit_window(cfg_bad), error = function(e) e), "error"))
days_all <- format(seq(as.Date("2017-01-01"), as.Date("2026-11-15"), by = "day"), "%Y-%m-%d")
sel <- days_all[in_fit_window(days_all, fw)]
ok("in_fit_window: chi giu ngay trong giai doan fit", max(sel) == fw[2] && min(sel) == fw[1])
ok("assert_no_test_days: du lieu fit hop le -> khong loi",
   isTRUE(assert_no_test_days(sel, cfg$test_from)))
ok("assert_no_test_days: co ngay >= mo'c test -> dung (stop)",
   inherits(tryCatch(assert_no_test_days(c(sel, cfg$test_from), cfg$test_from),
                     error = function(e) e), "error"))
# harmonic.R khong con viet cung nam fit va goi kiem tra truoc khi fit
src_h <- readLines(file.path(repo, "pipelines", "r", "harmonic.R"), warn = FALSE)
ok("harmonic.R: khong con FIT_YEARS viet cung", !any(grepl("FIT_YEARS\\s*<-", src_h)))
ok("harmonic.R: goi assert_no_test_days truoc fit_harmonics",
   {i_a <- grep("assert_no_test_days(d$obs_day", src_h, fixed = TRUE)
    i_f <- grep("fit <- fit_harmonics(t, d$value", src_h, fixed = TRUE)
    length(i_a) == 1 && length(i_f) == 1 && i_a < i_f})

# ---------------------------------------------------------------------------
cat("\n=== KET QUA ===\n")
cat("PASS: ", n_pass, "   FAIL: ", n_fail, "\n")
if (n_fail > 0) {
  cat("=> CO TEST KHONG DUNG.\n")
  quit(status = 1)
}
cat("=> TAT CA TEST DUNG.\n")
quit(status = 0)
