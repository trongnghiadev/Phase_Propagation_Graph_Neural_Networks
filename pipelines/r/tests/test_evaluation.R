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
  # A luon du bao cao hon 0.15 m, B luon thap hon 0.15 m -> hieu loss luon duong
  h_q50 = c(rep(1.55, n_extra), rep(1.25, n_extra)),
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
cat("\n=== KET QUA ===\n")
cat("PASS: ", n_pass, "   FAIL: ", n_fail, "\n")
if (n_fail > 0) {
  cat("=> CO TEST KHONG DUNG.\n")
  quit(status = 1)
}
cat("=> TAT CA TEST DUNG.\n")
quit(status = 0)
