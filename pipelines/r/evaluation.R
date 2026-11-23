#!/usr/bin/env Rscript
# evaluation.R -- Chay ham danh gia CHUNG cho MOI mo hinh (Chuong 4, muc 4.5).
#
#   Rscript pipelines/r/evaluation.R [--pred PATH ...] [--obs-only]
#
# LAM GI:
#  1. Doc du bao chinh thuc (KTTV) tu `official_forecasts` -> baseline `B_KTTV`.
#  2. Sinh du bao "moc" cho cac mo hinh co san de kiem chung pipeline:
#       - B0     : climatology (trung binh cac dinh cung ngay lich trong lich su)
#       - B_harm : trieu thien van du tinh (tu `harmonic.R`)
#       - B_KTTV : du bao chinh thuc
#  3. Tinh day du chi so: MAE, RMSE, bias (DeltaH), DeltaT, NSE, KGE,
#     POD/FAR/CSI (bao dong), skill so voi KTTV.
#  4. Kiem dinh: Wilcoxon theo cap, Diebold-Mariano, hieu chinh Holm.
#
# GHI CHU: `evaluation.R` (R/evaluation.R) la ham dung chung -- MOI mo hinh
# (ke ca PhaseGNN do worker khac viet) chi can xuat ban ghi theo
# `schemas/prediction/v1.schema.json` roi chay lai script nay de danh gia.
#
# DAU RA:
#   results/E5/evaluation_overall.csv
#   results/E5/evaluation_by_lead.csv
#   results/E5/evaluation_by_peak.csv
#   results/E5/evaluation_by_station.csv
#   results/E5/evaluation_contingency.csv
#   results/E5/evaluation_tests.csv
#   results/E5/evaluation_matched_sample.csv
#   results/E5/evaluation_*.png
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
source(file.path(here, "R", "evaluation.R"))
source(file.path(here, "R", "theme.R"))

use_repo_rlib(repo)
suppressPackageStartupMessages(library(ggplot2))

OUT_E5 <- p_res("E5")
ensure_dir(OUT_E5)

# ---------------------------------------------------------------------------
# 1. Thuc do
# ---------------------------------------------------------------------------
say2("== Doc du lieu thuc do ==")
obs <- observed_peak_table()
say2("bang thuc do: ", nrow(obs), " dong (tram x ngay quan sat); ",
     length(unique(obs$station)), " tram; ngay ",
     min(obs$day), " -> ", max(obs$day))

# Nguong bao dong theo thoi gian (cho POD/FAR/CSI)
say2("\n== Nguong bao dong theo thoi gian ==")
stations <- load_stations()
days_by_station <- split(obs$day, obs$station)
names(days_by_station) <- names(days_by_station)
thr <- alarm_thresholds(stations, days_by_station)
say2("bang nguong: ", nrow(thr), " dong")
# ban ghi nguong theo khoa "station|day"
#
# LUU Y HIEU NANG: KHONG duyet `thr_list[[key]] <- ...` 32 352 lan -- R sao chep
# toan bo list moi them 1 phan tu -> O(n^2) (chay > 5 phut). Thay vao do:
#   - tao list DA TIEN KICH THUOC (`vector("list", n)`) va gan theo chi so;
#   - sau do rut gon bang `unname()` cho cac khoa thuoc nghieu nhat.
thr_key <- paste(thr$station, thr$day, sep = "|")
thr_list <- vector("list", nrow(thr))
for (i in seq_len(nrow(thr))) {
  thr_list[[i]] <- list(I = thr$l1[i], II = thr$l2[i], III = thr$l3[i])
}
names(thr_list) <- thr_key
exceed_thresholds <<- thr_list

# muc bao dong thuc te cua ngay (theo dinh cao nhat)
# VECTOR HOA: dung `match` de tra nguong theo khoa "tram|ngay" (O(n log n)
# thay vi tra cuu list O(n) cho tung dong -> O(n^2)).
thr_key <- paste(thr$station, thr$day, sep = "|")
obs_key <- paste(obs$station, obs$day, sep = "|")
thr_ix <- match(obs_key, thr_key)
has_thr <- !is.na(thr_ix)
l1v <- rep(NA_real_, nrow(obs)); l2v <- l1v; l3v <- l1v
l1v[has_thr] <- thr$l1[thr_ix[has_thr]]
l2v[has_thr] <- thr$l2[thr_ix[has_thr]]
l3v[has_thr] <- thr$l3[thr_ix[has_thr]]
h_day <- pmax(obs$obs_h1, obs$obs_h2, na.rm = TRUE)
h_day[!is.finite(h_day)] <- NA_real_
obs_exceed <- rep("", nrow(obs))
obs_exceed[!is.na(l3v) & h_day >= l3v] <- "III"
obs_exceed[!is.na(l2v) & h_day >= l2v & obs_exceed != "III"] <- "II"
obs_exceed[!is.na(l1v) & h_day >= l1v & obs_exceed == ""] <- "I"
names(obs_exceed) <- obs_key

# ---------------------------------------------------------------------------
# 2. Cac mo hinh co san de kiem chung pipeline
# ---------------------------------------------------------------------------
preds <- list()

# --- B_KTTV: du bao chinh thuc ---
say2("\n== Du bao chinh thuc (B_KTTV) ==")
ktv <- load_official_forecasts()
if (!is.null(ktv)) {
  ktv$issued_date <- substr(ktv$issued_date, 1, 10)
  ktv$target_date <- substr(ktv$target_date, 1, 10)
  ktv <- ktv[!is.na(ktv$lead_day) & ktv$lead_day >= 1 & ktv$lead_day <= 5, , drop = FALSE]
  say2("B_KTTV: ", nrow(ktv), " ban ghi")
  preds[["B_KTTV"]] <- ktv
}

# --- B0: climatology (trung binh cac dinh cung ngay lich trong lich su) ---
say2("\n== Du bao climatology (B0) ==")
clim_parts <- lapply(split(obs, obs$station), function(d) {
  d$md <- format(as.Date(d$day), "%m-%d")
  d$yr <- as.integer(substr(d$day, 1, 4))
  # trung binh theo (md) tren cac nam truoc, loai bo nam du bao
  g <- aggregate(cbind(obs_h1, obs_h2) ~ md + yr, data = d, FUN = mean, na.rm = TRUE)
  m <- aggregate(cbind(obs_h1, obs_h2) ~ md, data = g, FUN = mean, na.rm = TRUE)
  names(m) <- c("md", "obs_h1", "obs_h2")
  m$station <- d$station[1]
  m
})
clim <- do.call(rbind, clim_parts)
clim_pred <- list()
for (st in unique(clim$station)) {
  m <- clim[clim$station == st, , drop = FALSE]
  for (y in 2017:2026) {
    d <- obs[obs$station == st & as.integer(substr(obs$day, 1, 4)) == y, , drop = FALSE]
    if (!nrow(d)) next
    d$md <- format(as.Date(d$day), "%m-%d")
    j <- match(d$md, m$md)
    # VECTOR HOA: 2 dong moi ngay (peak_idx 0/1) -> tao data.frame 1 lan.
    h1v <- m$obs_h1[j]; h2v <- m$obs_h2[j]
    keep <- !is.na(h1v) | !is.na(h2v)
    if (!any(keep)) next
    d <- d[keep, , drop = FALSE]; h1v <- h1v[keep]; h2v <- h2v[keep]
    n <- nrow(d)
    clim_pred[[length(clim_pred) + 1L]] <- data.frame(
      model = "B0", station = st,
      issued_date = rep(d$day, times = 2),
      target_date = rep(d$day, times = 2),
      lead_day = 1L, peak_idx = rep(c(0L, 1L), each = n),
      h_q10 = NA_real_, h_q50 = c(h1v, h2v), h_q90 = NA_real_,
      t_peak = NA_character_, stringsAsFactors = FALSE)
  }
}
if (length(clim_pred)) {
  clim_df <- do.call(rbind, clim_pred)
  # gio dinh climatology = trung binh gio dinh thuc do cung ngay lich
  clim_df$t_peak_vn <- NA
  say2("B0: ", nrow(clim_df), " ban ghi")
  preds[["B0"]] <- clim_df
}

# --- B_harm: trieu thien van du tinh ---
say2("\n== Du bao trieu thien van (B_harm) ==")
# UU TIEN 1: bang trieu thien van THEO TRAM (`results/E1/astro_tide_by_station.csv`)
#   -> danh gia cong bang cho tung tram (khac nhau ve muc datum).
# UU TIEN 2: `data/processed/features/astro_tide.csv` (chi Vung Tau) -> dung
#   khi chua co bang theo tram.
astro_st_path <- file.path(p_res("E1"), "astro_tide_by_station.csv")
astro_path <- p_data("processed", "features", "astro_tide.csv")
use_station_ast <- file.exists(astro_st_path)
if (use_station_ast) {
  ast <- read_csv_df(astro_st_path)
  ast$station <- as.character(ast$station)
} else {
  ast <- read_csv_df(astro_path)
  ast$station <- NA_character_
}
if (nrow(ast)) {
  ast$day <- substr(as.character(ast$day), 1, 10)
  ast$h1 <- as.numeric(ast$h1); ast$h2 <- as.numeric(ast$h2)
  ast$t1_h <- as.numeric(ast$t1_h); ast$t2_h <- as.numeric(ast$t2_h)
  # VECTOR HOA: ghep (station, day) -> 2 dong (peak_idx 0/1) mot lan.
  # (Vong lap "for st ... for i in seq_len(nrow(obs))" la O(n^2) -- qua 700 gio
  #  cho 32k dong; phai ghep bang `merge`.)
  # tao bang moi dong cho 2 dinh cua astro_tide
  ast_long <- data.frame(
    station = if (use_station_ast) ast$station else NA_character_,
    day = rep(ast$day, times = 2),
    peak_idx = rep(c(0L, 1L), each = nrow(ast)),
    h = c(ast$h1, ast$h2),
    t_h = c(ast$t1_h, ast$t2_h),
    stringsAsFactors = FALSE)
  ast_long <- ast_long[!is.na(ast_long$h), , drop = FALSE]
  # join: obs x astro theo (station, day)
  obs2 <- data.frame(
    station = obs$station, day = obs$day,
    obs_row = seq_len(nrow(obs)), stringsAsFactors = FALSE)
  if (use_station_ast) {
    jn <- merge(obs2, ast_long, by = c("station", "day"), all.x = TRUE, sort = FALSE)
  } else {
    jn <- merge(obs2, ast_long, by = "day", all.x = TRUE, sort = FALSE)
    jn$station <- jn$station.x
    jn$station.x <- NULL; jn$station.y <- NULL
  }
  jn <- jn[order(jn$obs_row, jn$peak_idx), , drop = FALSE]
  jn <- jn[!is.na(jn$h) & !is.na(jn$t_h), , drop = FALSE]
  if (nrow(jn)) {
    # moc 00:00 (gio Viet Nam) cua moi ngay -> vector hoa
    t0_all <- as.POSIXct(paste0(jn$day, " 00:00:00"), tz = "Asia/Ho_Chi_Minh",
                         format = "%Y-%m-%d %H:%M:%S")
    tp_all <- t0_all + jn$t_h * 3600
    hd <- data.frame(
      model = "B_harm", station = jn$station,
      issued_date = jn$day, target_date = jn$day, lead_day = 1L,
      peak_idx = jn$peak_idx,
      h_q10 = NA_real_, h_q50 = jn$h, h_q90 = NA_real_,
      t_peak = format(tp_all - 7 * 3600, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
      stringsAsFactors = FALSE)
    hd$t_peak_vn <- parse_t_peak_utc(hd$t_peak)
    say2("B_harm: ", nrow(hd), " ban ghi (nguon: ",
         if (use_station_ast) "trieu thien van theo tram" else "Vung Tau", ")")
    preds[["B_harm"]] <- hd
  }
} else {
  say2("[CANH BAO] khong co ", astro_path, " -> bo qua B_harm")
}

# Gom tat ca thanh mot bang du bao
all_pred <- do.call(rbind, lapply(preds, function(d) {
  if (is.null(d$t_peak_vn)) d$t_peak_vn <- parse_t_peak_utc(d$t_peak)
  d
}))
rownames(all_pred) <- NULL
say2("\ntong so ban ghi du bao: ", nrow(all_pred))

# ---------------------------------------------------------------------------
# 3. Danh gia
# ---------------------------------------------------------------------------
say2("\n== Danh gia ==")
ev <- evaluate_predictions(all_pred, obs, exceed_thresholds = thr_list,
                           obs_exceed = obs_exceed)
m <- ev$matched
say2("so cap ghep duoc: ", nrow(m), " / ", nrow(all_pred), " ban ghi")

if (!is.null(ev$overall)) {
  write_csv_df(ev$overall, file.path(OUT_E5, "evaluation_overall.csv"))
  write_csv_df(ev$by_lead, file.path(OUT_E5, "evaluation_by_lead.csv"))
  write_csv_df(ev$by_peak, file.path(OUT_E5, "evaluation_by_peak.csv"))
  write_csv_df(ev$by_station, file.path(OUT_E5, "evaluation_by_station.csv"))
  write_csv_df(ev$contingency, file.path(OUT_E5, "evaluation_contingency.csv"))
  write_csv_df(m, file.path(OUT_E5, "evaluation_matched_sample.csv"))
  if (!is.null(ev$tests$wilcoxon)) {
    write_csv_df(ev$tests$wilcoxon, file.path(OUT_E5, "evaluation_wilcoxon.csv"))
  }
  if (!is.null(ev$tests$dm)) {
    write_csv_df(ev$tests$dm, file.path(OUT_E5, "evaluation_diebold_mariano.csv"))
  }
  if (!is.null(ev$tests)) {
    tt <- do.call(rbind, c(
      if (!is.null(ev$tests$wilcoxon))
        cbind(test = "wilcoxon", ev$tests$wilcoxon[, c("model_a", "model_b", "n_pairs",
          "stat_w", "p_value", "p_holm")], stringsAsFactors = FALSE),
      if (!is.null(ev$tests$dm))
        cbind(test = "diebold_mariano", ev$tests$dm[, c("model_a", "model_b", "n",
          "dm_t", "p_value", "p_holm")], stringsAsFactors = FALSE)))
    write_csv_df(tt, file.path(OUT_E5, "evaluation_tests.csv"))
  }
  say2("\n-- Chi so tong hop --")
  print(ev$overall)
  if (!is.null(ev$contingency)) {
    say2("\n-- Chi so bao dong --")
    print(ev$contingency)
  }
  if (!is.null(ev$tests$wilcoxon)) {
    say2("\n-- Wilcoxon (co Holm) --")
    print(ev$tests$wilcoxon[, c("model_a", "model_b", "n_pairs", "stat_w",
                                "p_value", "p_holm")])
  }
  if (!is.null(ev$tests$dm)) {
    say2("\n-- Diebold-Mariano (co Holm) --")
    print(ev$tests$dm[, c("model_a", "model_b", "n", "dm_t", "p_value", "p_holm")])
  }
}

# ---------------------------------------------------------------------------
# 4. Hinh
# ---------------------------------------------------------------------------
if (!is.null(ev$overall) && nrow(ev$overall)) {
  ov <- ev$overall
  p1 <- ggplot(ov, aes(x = model, y = mae, fill = model)) +
    geom_col(width = 0.65) +
    geom_text(aes(label = sprintf("%.3f", mae)), vjust = -0.4, size = 3.4) +
    scale_y_continuous(expand = expansion(mult = c(0, 0.15))) +
    scale_fill_manual(values = palette_pgnn(nrow(ov))) +
    labs(title = "MAE do cao dinh trieu theo mo hinh",
         subtitle = "Ghep du bao - thuc do trong ±6 gio; mau trung vi (h_q50)",
         x = NULL, y = "MAE (m)") +
    theme_pgnn()
  save_fig(p1, file.path(OUT_E5, "evaluation_mae_by_model.png"), width = 1120, height = 800)
}
if (!is.null(ev$by_lead) && nrow(ev$by_lead)) {
  p2 <- ggplot(ev$by_lead, aes(x = factor(lead_day), y = mae, color = model,
                                group = model)) +
    geom_line(linewidth = 0.6) + geom_point(size = 2) +
    scale_color_manual(values = palette_pgnn(length(unique(ev$by_lead$model)))) +
    labs(title = "MAE theo chan troi (lead_day)",
         subtitle = "lead_day = 1 la ngay ban tin, 5 la ngay du bao xa nhat",
         x = "Lead day", y = "MAE (m)", color = "Mo hinh") +
    theme_pgnn()
  save_fig(p2, file.path(OUT_E5, "evaluation_mae_by_lead.png"), width = 1280, height = 800)
}
if (!is.null(ev$by_station) && nrow(ev$by_station)) {
  p3 <- ggplot(ev$by_station, aes(x = station, y = mae, fill = model)) +
    geom_col(position = position_dodge(width = 0.85), width = 0.8) +
    scale_fill_manual(values = palette_pgnn(length(unique(ev$by_station$model)))) +
    labs(title = "MAE theo tram", x = NULL, y = "MAE (m)", fill = "Mo hinh") +
    theme_pgnn() + theme(axis.text.x = element_text(angle = 30, hjust = 1))
  save_fig(p3, file.path(OUT_E5, "evaluation_mae_by_station.png"), width = 1600, height = 800)
}

say2("\n================ KET QUA evaluation.R ================")
say2("bang thuc do: ", nrow(obs), " dong")
say2("ban ghi du bao: ", nrow(all_pred))
say2("cap ghep duoc: ", nrow(m))
if (!is.null(ev$overall)) {
  for (i in seq_len(nrow(ev$overall))) {
    r <- ev$overall[i, ]
    say2(sprintf("  %-8s n=%6d  MAE=%.4f m  RMSE=%.4f  bias=%+.4f  MAE_t=%.3f h  NSE=%.4f  KGE=%.4f",
                 r$model, r$n, r$mae, r$rmse, r$bias, r$mae_t_h, r$nse, r$kge))
  }
}
say2("=======================================================")
