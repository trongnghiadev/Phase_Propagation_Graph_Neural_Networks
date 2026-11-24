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
# Dung du lieu gia lap: 2 dinh/ngay lech ~12.42 h (mot chu ky M2).
# M2 = 12.4206 h. Cho 4 ngay lien tiep, moi ngay 2 dinh:
#   dinh chinh (gia tri cao) va dinh phu (gia tri thap) cach nhau 12.42 h.
mk_fake_tide <- function(station, n_days = 4L, start = "2024-01-01",
                         period_h = 12.4206012) {
  base <- as.POSIXct(paste0(start, " 00:00:00"), tz = "Asia/Ho_Chi_Minh")
  rows <- list()
  for (i in seq_len(n_days)) {
    for (k in 0:1) {
      ts <- base + (i - 1) * 86400 + k * period_h * 3600
      rows[[length(rows) + 1L]] <- data.frame(
        station = station, kind = "high", ts = ts,
        ts_hour = as.numeric(format(ts, "%H", tz = "Asia/Ho_Chi_Minh")) +
          as.numeric(format(ts, "%M", tz = "Asia/Ho_Chi_Minh")) / 60,
        value = if (k == 0L) 2.0 else 1.0,
        obs_day = format(base + (i - 1) * 86400, "%Y-%m-%d"),
        day_offset = 0L,
        stringsAsFactors = FALSE)
    }
  }
  do.call(rbind, rows)
}
fake_tide <- rbind(mk_fake_tide("VUNG_TAU"), mk_fake_tide("STATION_A"))
# `pivot_extremes()` can cot `rn` (sinh tu `build_peak_table()`).
fake_tide <- fake_tide[order(fake_tide$station, fake_tide$obs_day, fake_tide$ts), ]
fake_tide$rn <- ave(seq_len(nrow(fake_tide)),
                    list(fake_tide$station, fake_tide$kind, fake_tide$obs_day),
                    FUN = seq_along)

# Quy tac CU (pivot_extremes): dinh 1 = gio nho nhat trong ngay quan sat
fake_pk <- pivot_extremes(fake_tide, "peak")
ok("pivot_extremes: giu nguyen quy tac thu tu gio trong ngay (R-Spark parity)",
   fake_pk$t1_h[1] <= fake_pk$t2_h[1])
ok("pivot_extremes: 2 dinh -> 1 dong cho moi (tram, ngay)",
   nrow(fake_pk) == 8 && all(!is.na(fake_pk$h1)) && all(!is.na(fake_pk$h2)))

# Quy tac MOI (the he chu ky trieu) - gan cho TUNG TRAM
anchor_ts <- select_cycle_anchors(fake_tide[fake_tide$station == "VUNG_TAU", ])
gen <- assign_peak_generation(fake_tide[fake_tide$station == "STATION_A", ],
                               phase_ref = 180, period_h = per_m2,
                               n_per_gen = 2L, gen_anchor = anchor_ts)
ok("assign_peak_generation: moi the he chu ky co <= 2 dinh",
   all(table(gen$tide_gen) <= 2))
ok("assign_peak_generation: dinh thu 1 la dinh co gia tri CAO NHAT",
   all(gen$value[gen$gen_rn == 1] == 2.0))
ok("assign_peak_generation: dinh thu 2 la dinh gia tri THAP",
   all(gen$value[gen$gen_rn == 2] == 1.0))
ok("assign_peak_generation: so the he = 4 (4 ngay x 1 the he/ngay)",
   length(unique(gen$tide_gen)) == 4)

# Kiem tra dinh 1/2 CUNG THE HE (nghia la 2 dinh cach nhau ~1 chu ky M2)
g1 <- gen[gen$gen_rn == 1, ]
g2 <- gen[gen$gen_rn == 2, ]
ok("assign_peak_generation: dinh 1 va dinh 2 thuoc CUNG the he chu ky",
   nrow(g1) == 4 && nrow(g2) == 4 && all(g1$tide_gen == g2$tide_gen))
# Do lech thoi gian tuong doi (dung `ts`, khong dung `ts_hour` de tranh wrap
# qua 24h) -> phai bang 1 chu ky M2.
m_g <- merge(g1[, c("station", "ts", "tide_gen")],
             g2[, c("station", "ts", "tide_gen")],
             by = c("station", "tide_gen"))
dt_h <- abs(as.numeric(difftime(m_g$ts.x, m_g$ts.y, units = "hours")))
ok("assign_peak_generation: 2 dinh cach nhau ~1 chu ky M2 (12.42 h)",
   nrow(m_g) == 4 && all(abs(dt_h - per_m2) < 0.01))

# Kiem tra tinh hop ly: ghep 2 tram theo the he -> do tre = 0 khi cung pha
gen_ser <- peak_series_by_generation(fake_tide, "STATION_A",
                                      fake_tide[fake_tide$station == "VUNG_TAU", ],
                                      phase_ref = 180, period_h = per_m2,
                                      n_per_gen = 2L)
ok("peak_series_by_generation: tra ve 2 chuoi (peak1, peak2)",
   length(gen_ser) == 2 && !is.null(gen_ser[[1]]) && !is.null(gen_ser[[2]]))
ok("peak_series_by_generation: moi cap co 1 dong the he chu ky",
   nrow(gen_ser[[1]]) == 4 && nrow(gen_ser[[2]]) == 4)
ok("peak_series_by_generation: do tre ~ 0 khi 2 tram cung pha",
   abs(stats::median(((gen_ser[[1]]$t_h - gen_ser[[1]]$t_ref_h + 12) %% 24) - 12)) < 1e-9)

# CAU HOI THEN CUOT: bien do lon lam dinh CHINH "roi" sang khung 23-24h.
#
# Mo phong: tren tram STATION_A, dinh chinh (gia tri 2.0) thuong xay ra luc
# ~00:00. Do bien do bien doi, dinh chinh cua chu ky 2024-01-02 xay ra luc
# 23:30 cua ngay 2024-01-01 (dinh bien do lon 3.0). Cac dinh con lai giu
# nguyen.
#
# VOI QUY TAC CU (theo gio trong ngay quan sat): ngay 2024-01-01 se chua 2
# dinh {00:00 (2.0), 23:30 (3.0)} -> "dinh 1" = 00:00, "dinh 2" = 23:30 ->
# cap dinh cach nhau 23.5 h (SAI CHU KY, phai la ~12.42 h) -> do tre bi am.
# Ngoai ra con: ngay 2024-01-02 chi con 1 dinh (12:25) -> mat cap dinh.
# MO PHONG DAI TIENG: ca chu ky 2024-01-02 bi "roi" 0.5 h -> dinh CHINH
# (2.0) chuyen tu 00:00 cua 01-02 sang 23:30 cua 01-01, va dinh PHU (1.0)
# chuyen tu 12:25 cua 01-02 sang 12:25 cua 01-01. Voi bien do lon, dinh
# chinh co the "roi" nhu vay (bien do khong bat bien).
rolled_ts <- as.POSIXct("2024-01-01 23:30:00", tz = "Asia/Ho_Chi_Minh")
rolled <- fake_tide
# Xoa CAP dinh cua ngay 2024-01-02 (00:00 va 12:25) cua STATION_A
rolled <- rolled[!(rolled$station == "STATION_A" &
                     rolled$obs_day == "2024-01-02"), , drop = FALSE]
# Them 2 dinh "roi" vao ngay 2024-01-01
rolled <- rbind(rolled, data.frame(
  station = "STATION_A", kind = "high", ts = rolled_ts,
  ts_hour = 23.5, value = 3.0, obs_day = "2024-01-01",
  day_offset = 0L, rn = NA_integer_, stringsAsFactors = FALSE))
rolled <- rbind(rolled, data.frame(
  station = "STATION_A", kind = "high",
  ts = as.POSIXct("2024-01-01 12:25:14", tz = "Asia/Ho_Chi_Minh"),
  ts_hour = 12.41667, value = 1.0, obs_day = "2024-01-01",
  day_offset = 0L, rn = NA_integer_, stringsAsFactors = FALSE))
rolled <- rolled[order(rolled$station, rolled$obs_day, rolled$ts), ]
rolled$rn <- ave(seq_len(nrow(rolled)),
                 list(rolled$station, rolled$kind, rolled$obs_day),
                 FUN = seq_along)
pk_shift <- pivot_extremes(rolled[rolled$station == "STATION_A", ], "peak")
# Quy tac CU: ngay 2024-01-01 bay gio co 3 dinh (00:00, 12:25, 23:30).
# "dinh 1" = 00:00, "dinh 2" = 12:25 -> cap dinh van dung, NHUNG dinh bien do
# lon 23:30 bi BO (rn=3) -> mat dinh chinh thuc te cua chu ky.
# Ngay 2024-01-02 mat het dinh -> khong co cap dinh nao.
d01 <- pk_shift[pk_shift$day == "2024-01-01", ]
ok("Quy tac CU: dinh bien do lon 23:30 bi BO (rn=3) trong ngay 01-01",
   nrow(d01) == 1 && abs(d01$t1_h - 0) < 1e-9 && abs(d01$t2_h - 12.41667) < 1e-3)
ok("Quy tac CU: ngay 01-02 mat het cap dinh (khong con dinh nao)",
   nrow(pk_shift[pk_shift$day == "2024-01-02", ]) == 0)

# Quy tac MOI (the he chu ky): moi dinh van thuoc DUNG the he chu ky cua
# tram tham chieu. Dinh "roi" 23:30 thuoc the he CUA CHINH NO (khong bi "gop"
# sai vao the he khac) -> cap dinh van cach nhau ~12.42 h.
anchor_rolled <- select_cycle_anchors(fake_tide[fake_tide$station == "VUNG_TAU", ])
gen_shift <- assign_peak_generation(rolled[rolled$station == "STATION_A", ],
                                     phase_ref = 180, period_h = per_m2,
                                     n_per_gen = 2L,
                                     gen_anchor = anchor_rolled)
ok("Quy tac MOI: moi dinh thuoc mot the he cua tram tham chieu",
   all(gen_shift$tide_gen >= 1) && length(unique(gen_shift$tide_gen)) == 4)
ok("Quy tac MOI: moi the he chu ky van co <= 2 dinh",
   all(table(gen_shift$tide_gen) <= 2))
# Do tre giua 2 dinh cua cung the he van la ~1 chu ky M2 (12.42 h)
g1s <- gen_shift[gen_shift$gen_rn == 1, ]
g2s <- gen_shift[gen_shift$gen_rn == 2, ]
m_s <- merge(g1s[, c("station", "ts", "tide_gen")],
             g2s[, c("station", "ts", "tide_gen")],
             by = c("station", "tide_gen"))
dt_s <- abs(as.numeric(difftime(m_s$ts.x, m_s$ts.y, units = "hours")))
ok("Quy tac MOI: 2 dinh cung the he cach nhau ~1 chu ky M2 (12.42 h)",
   nrow(m_s) == 4 && all(abs(dt_s - per_m2) < 0.01))
# DINH "ROI" (23:30) va dinh PHU (12:25) cua cung chu ky phai cung the he,
# va dinh CHINH cua chu ky do (gia tri 3.0) la dinh thu 1.
g_roll <- gen_shift[abs(gen_shift$ts_hour - 23.5) < 1e-9, ]
g_sec <- gen_shift[abs(gen_shift$ts_hour - 12.41667) < 1e-3, ]
ok("Quy tac MOI: dinh 'roi' 23:30 va dinh phu 12:25 cung the he",
   nrow(g_roll) == 1 && nrow(g_sec) == 1 &&
   g_roll$tide_gen == g_sec$tide_gen)
ok("Quy tac MOI: dinh bien do lon 23:30 la dinh thu 1 cua the he",
   nrow(g_roll) == 1 && g_roll$gen_rn == 1 && g_roll$value == 3.0)

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
