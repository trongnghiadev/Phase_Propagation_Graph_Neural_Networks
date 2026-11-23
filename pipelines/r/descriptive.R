#!/usr/bin/env Rscript
# descriptive.R -- Thong ke mo ta (Chuong 4, muc 4.2).
#
#   Rscript pipelines/r/descriptive.R
#
# LAM GI:
#  1. PHAN PHOI DINH NGAY/THANG/NAM theo tung tram (tu `tide_extremes` + ban tin).
#  2. MUA TRIEU CUONG: thang nao co dinh cao nhat -> phan bo dinh theo thang.
#  3. TAN SUAT VUOT BA DO I/II/III THEO NAM -- dung `stations.alarm_history`
#     (nguong THAY DOI THEO THOI GIAN, khong dung nguong co dinh).
#  4. HO CHUA: muc nuoc, luu luong vao, xa theo mua; cac DOT XA TRAN.
#  5. MUA: luong mua ngay, theo mua, tai cac tram du so lieu.
#  6. XU HUONG: Mann-Kendall + do docc Sen cho dinh nam o PHU AN va NHA BE.
#  7. PHAN PHOI THUOC TINH MERIT trong cay 44045960.
#  8. NGOAI LEI: IQR/MAD tren dinh trieu theo tram-thang -> hang doi kiem duyet.
#
# DAU RA -> results/E8/ (CSV + PNG).
#
# Luat phu thuoc (ADR 0001): khong goi Python, khong import tu `packages/`.
# Logic dung chung nam trong `pipelines/r/R/`.

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
source(file.path(here, "R", "theme.R"))
# LUU Y: KHONG source `R/data.R` o day -- file do dinh nghia lai mot so ham
# cung ten voi `R/rules.R` (vd `load_tide_extremes`) -> source ca hai se tao
# xung dot. `R/rules.R` la ban chuan cho GĐ4 (quy tac ngay dinh + tai du lieu
# trieu dung chung cho harmonic.R / phase_lag.R / evaluation.R).

use_repo_rlib(repo)
suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
})

# Tien ich: trung binh / lon nhat bo qua NA (va tra NA khi rong).
safe_mean <- function(x) if (all(is.na(x))) NA_real_ else mean(x, na.rm = TRUE)
safe_max  <- function(x) if (all(is.na(x))) NA_real_ else max(x, na.rm = TRUE)

OUT <- file.path(repo, "results", "E8")
ensure_dir(OUT)

# ---------------------------------------------------------------------------
# 0. TAI DU LIEU
# ---------------------------------------------------------------------------
say("[E8] Doc `tide_extremes` ...")
tide <- load_tide_extremes(qc_ok_only = TRUE)
say("[E8] Doc `hydromet_bulletins` ...")
bul <- load_bulletin_days()

pt <- build_peak_table(tide, bul)
pk <- pivot_extremes(pt, "peak")
lo <- pivot_extremes(pt, "low")
cat("[E8] ban tin quan sat:", length(unique(pk$day)), "ngay |",
    length(unique(pk$station)), "tram\n")

st <- load_stations()
cat("[E8] tram:", nrow(st), "\n")

# ---------------------------------------------------------------------------
# 1. PHAN PHOI DINH THEO NGAY / THANG / NAM (tung tram)
# ---------------------------------------------------------------------------
# Long gom dinh 1 + dinh 2 thanh mot bang "dinh" de ve phan bo.
all_pk <- rbind(
  data.frame(station = pk$station, day = pk$day, kind = "peak1",
             h = pk$h1, t_h = pk$t1_h, stringsAsFactors = FALSE),
  data.frame(station = pk$station, day = pk$day, kind = "peak2",
             h = pk$h2, t_h = pk$t2_h, stringsAsFactors = FALSE))
all_pk <- all_pk[!is.na(all_pk$h), , drop = FALSE]
all_pk$year <- as.integer(substr(all_pk$day, 1, 4))
all_pk$month <- as.integer(substr(all_pk$day, 6, 7))
all_pk$mday <- as.integer(substr(all_pk$day, 9, 10))
all_pk$hour <- all_pk$t_h
all_pk <- all_pk[order(all_pk$station, all_pk$day, all_pk$kind), , drop = FALSE]
rownames(all_pk) <- NULL

# 1a. Phan bo theo NAM
by_year <- all_pk %>%
  group_by(station, year) %>%
  summarise(n = n(), h_mean = mean(h), h_median = median(h),
            h_sd = sd(h), h_min = min(h), h_max = max(h),
            .groups = "drop")

# 1b. Phan bo theo THANG
by_month <- all_pk %>%
  group_by(station, month) %>%
  summarise(n = n(), h_mean = mean(h), h_median = median(h),
            h_sd = sd(h), h_max = max(h),
            .groups = "drop")

# 1c. Phan bo theo NGAY trong thang (ngay 1..31)
by_mday <- all_pk %>%
  group_by(station, mday) %>%
  summarise(n = n(), h_mean = mean(h), h_median = median(h),
            .groups = "drop")

# 1d. Phan bo theo GIO cua dinh
by_hour <- all_pk %>%
  group_by(station, hour) %>%
  summarise(n = n(), h_mean = mean(h), .groups = "drop")

# 1e. Thong ke tong theo tram
by_station <- all_pk %>%
  group_by(station) %>%
  summarise(n_dinhs = n(), n_days = n_distinct(day),
            h_mean = mean(h), h_median = median(h), h_sd = sd(h),
            h_min = min(h), h_max = max(h),
            h_p05 = quantile(h, 0.05), h_p95 = quantile(h, 0.95),
            year_min = min(year), year_max = max(year),
            .groups = "drop")

write_csv_df(by_station, file.path(OUT, "e8_peak_summary_by_station.csv"))
write_csv_df(by_year,    file.path(OUT, "e8_peak_by_year.csv"))
write_csv_df(by_month,   file.path(OUT, "e8_peak_by_month.csv"))
write_csv_df(by_mday,    file.path(OUT, "e8_peak_by_mday.csv"))
write_csv_df(by_hour,    file.path(OUT, "e8_peak_by_hour.csv"))

save_fig({
  ggplot(by_year, aes(x = factor(year), y = h_median, color = station)) +
    geom_line(linewidth = 0.6) + geom_point(size = 1.8) +
    labs(title = "Dinh trieu trung viet theo nam",
         subtitle = "Duong = h1/h2 cua ban tin quan sat (gio Viet Nam)",
         x = "Nam", y = "Do cao dinh (m)", color = "Tram") +
    theme_pgnn()
}, file.path(OUT, "e8_peak_by_year.png"), width = 1100, height = 650)

save_fig({
  ggplot(by_month, aes(x = factor(month), y = h_median, color = station)) +
    geom_line(linewidth = 0.6) + geom_point(size = 1.8) +
    labs(title = "Dinh trieu trung viet theo thang",
         subtitle = "Dung de xac dinh mua trieu cuong",
         x = "Thang", y = "Do cao dinh (m)", color = "Tram") +
    theme_pgnn()
}, file.path(OUT, "e8_peak_by_month.png"), width = 1100, height = 650)

save_fig({
  ggplot(by_hour, aes(x = hour, y = h_mean, color = station)) +
    geom_line(linewidth = 0.6) +
    labs(title = "Phan bo do cao dinh theo gio trong ngay",
         subtitle = "Dinh trieu thuong xay o khoang 6h-8h va 18h-20h",
         x = "Gio (gio Viet Nam)", y = "Do cao dinh TB (m)", color = "Tram") +
    theme_pgnn()
}, file.path(OUT, "e8_peak_by_hour.png"), width = 1100, height = 650)

# ---------------------------------------------------------------------------
# 2. MUA TRIEU CUONG
# ---------------------------------------------------------------------------
# Mua trieu cuong = nhom thang co dinh cao nhat. Tinh bang trung binh dinh
# THU Nhat (dinh trieu) theo thang tren TAT CA tram (de co so chung chung).
flood_profile <- all_pk %>%
  filter(kind == "peak1") %>%
  group_by(month) %>%
  summarise(n = n(), h_mean = mean(h), h_median = median(h),
            h_max = max(h), .groups = "drop") %>%
  arrange(desc(h_median))

flood_season_months <- flood_profile$month[1:3]
flood_season_label <- paste(month.abb[flood_season_months], collapse = "-")
cat("[E8] mua trieu cuong (3 thang dinh cao nhat):", flood_season_label, "\n")

flood_by_station <- all_pk %>%
  filter(kind == "peak1") %>%
  mutate(in_flood_season = month %in% flood_season_months) %>%
  group_by(station, in_flood_season) %>%
  summarise(n = n(), h_mean = mean(h), h_median = median(h),
            h_max = max(h), .groups = "drop")

write_csv_df(flood_profile, file.path(OUT, "e8_flood_season_profile.csv"))
write_csv_df(flood_by_station, file.path(OUT, "e8_flood_season_by_station.csv"))

save_fig({
  ggplot(flood_profile, aes(x = factor(month, levels = 1:12), y = h_median)) +
    geom_col(fill = palette_pgnn1(1)) +
    geom_hline(yintercept = mean(flood_profile$h_median),
               linetype = "dashed", color = "#888888") +
    labs(title = "Mua trieu cuong: dinh trieu trung viet theo thang",
         subtitle = paste0("Mua trieu cuong (3 thang cao nhat): ", flood_season_label),
         x = "Thang", y = "Dinh trung viet (m)") +
    theme_pgnn()
}, file.path(OUT, "e8_flood_season.png"), width = 900, height = 600)

# ---------------------------------------------------------------------------
# 3. TAN SUAT VUOT BA DO I/II/III THEO NAM (NGUONG THEO THOI GIAN)
# ---------------------------------------------------------------------------
# `stations.alarm_history`: moi phan tu {from, to, level_1_m, level_2_m, level_3_m}.
# Moi ngay cua moi tram lay nguong DUNG THOI DIEM DO -> KHONG dung nguong co dinh.
days_by_station <- split(pk$day, pk$station)
names(days_by_station) <- unique(pk$station)
thr <- alarm_thresholds(st, days_by_station)
thr <- thr[!is.na(thr$station) & !is.na(thr$day), , drop = FALSE]
thr$year <- as.integer(substr(thr$day, 1, 4))
cat("[E8] nguong bao dong theo thoi gian:", nrow(thr), "dong tram-ngay |",
    sum(!is.na(thr$l1)), "co nguong I\n")

# dinh cao nhat trong ngay (dung de so sanh voi nguong bao dong)
day_max <- all_pk %>%
  group_by(station, day) %>%
  summarise(h_day_max = max(h), .groups = "drop")

alarm_df <- merge(thr, day_max, by = c("station", "day"), all.x = TRUE)
alarm_df <- alarm_df[!is.na(alarm_df$h_day_max), , drop = FALSE]
lv <- exceed_level_of(alarm_df$h_day_max, alarm_df$l1, alarm_df$l2, alarm_df$l3)
alarm_df$level <- lv$level
alarm_df$level_known <- lv$known

alarm_year <- alarm_df %>%
  filter(level_known) %>%
  group_by(station, year) %>%
  summarise(
    n_days = n(),
    n_I   = sum(level == "I"),
    n_II  = sum(level == "II"),
    n_III = sum(level == "III"),
    rate_I   = ifelse(n_days > 0, mean(level == "I"), NA_real_),
    rate_II  = ifelse(n_days > 0, mean(level == "II"), NA_real_),
    rate_III = ifelse(n_days > 0, mean(level == "III"), NA_real_),
    .groups = "drop")

alarm_year_all <- alarm_df %>%
  group_by(station, year) %>%
  summarise(
    n_days = n(),
    n_I   = sum(level == "I", na.rm = TRUE),
    n_II  = sum(level == "II", na.rm = TRUE),
    n_III = sum(level == "III", na.rm = TRUE),
    .groups = "drop")

write_csv_df(alarm_year, file.path(OUT, "e8_alarm_frequency_by_year.csv"))
write_csv_df(alarm_year_all, file.path(OUT, "e8_alarm_counts_by_year.csv"))

save_fig({
  d <- alarm_year %>% filter(station %in% head(unique(station), 6))
  ggplot(d, aes(x = factor(year), y = rate_I, color = station)) +
    geom_line(linewidth = 0.6) + geom_point(size = 1.8) +
    labs(title = "Tan suat vuot BA DO I theo nam",
         subtitle = "Nguong lay tu `stations.alarm_history` (thay doi theo thoi gian)",
         x = "Nam", y = "Ty le vuot BDI", color = "Tram") +
    theme_pgnn()
}, file.path(OUT, "e8_alarm_rate_I_by_year.png"), width = 1100, height = 650)

# ---------------------------------------------------------------------------
# 4. HO CHUA: MUC NUOC, LUU LUONG VAO, XA; DOT XA TRAN
# ---------------------------------------------------------------------------
res <- load_reservoir()
if (nrow(res)) {
  res$year <- as.integer(substr(res$day, 1, 4))
  res$month <- as.integer(substr(res$day, 6, 7))
  res$season <- season_of_month(res$month)
  # luu luong xa TONG (may + xa tran)
  res$q_release <- res$turbine_discharge_m3s + res$spillway_discharge_m3s
  res$spill_event <- !is.na(res$spillway_discharge_m3s) & res$spillway_discharge_m3s > 0
  res <- res[order(res$reservoir, res$day), , drop = FALSE]

  res_by_res <- res %>%
    group_by(reservoir) %>%
    summarise(
      n = n(),
      day_min = min(day), day_max = max(day),
      wl_mean = safe_mean(upstream_water_level_m),
      wl_max  = safe_max(upstream_water_level_m),
      inflow_mean  = safe_mean(inflow_m3s),
      inflow_max   = safe_max(inflow_m3s),
      release_mean = safe_mean(q_release),
      release_max  = safe_max(q_release),
      spill_mean   = safe_mean(spillway_discharge_m3s),
      n_spill_days = sum(spill_event, na.rm = TRUE),
      .groups = "drop")

  res_by_season <- res %>%
    group_by(reservoir, season) %>%
    summarise(
      n = n(),
      wl_mean = safe_mean(upstream_water_level_m),
      inflow_mean = safe_mean(inflow_m3s),
      inflow_max  = safe_max(inflow_m3s),
      release_mean = safe_mean(q_release),
      release_max  = safe_max(q_release),
      spill_mean   = safe_mean(spillway_discharge_m3s),
      n_spill_days = sum(spill_event, na.rm = TRUE),
      .groups = "drop")

  res_by_year <- res %>%
    group_by(reservoir, year) %>%
    summarise(
      n = n(),
      wl_mean = safe_mean(upstream_water_level_m),
      inflow_mean = safe_mean(inflow_m3s),
      inflow_max  = safe_max(inflow_m3s),
      release_mean = safe_mean(q_release),
      release_max  = safe_max(q_release),
      spill_mean   = safe_mean(spillway_discharge_m3s),
      n_spill_days = sum(spill_event, na.rm = TRUE),
      .groups = "drop")

  # Cac DOT XA TRAN (nhom ngay lien tiep co xa tran > 0)
  spill_events <- list()
  for (rs in unique(res$reservoir)) {
    d <- res[res$reservoir == rs, , drop = FALSE]
    d <- d[!is.na(d$spillway_discharge_m3s), , drop = FALSE]
    d <- d[order(d$day), , drop = FALSE]
    if (!nrow(d)) next
    # `day` la chuoi "YYYY-MM-DD" -> dung `as.Date` de tinh khoang cach ngay.
    # (`diff()` khong hoat dong tren character.)
    dte <- as.Date(d$day)
    grp <- cumsum(c(1, as.integer(diff(dte) > 1)))
    for (g in unique(grp)) {
      e <- d[grp == g, , drop = FALSE]
      if (!any(e$spillway_discharge_m3s > 0)) next
      spill_events[[length(spill_events) + 1L]] <- data.frame(
        reservoir = rs,
        start_day = e$day[1], end_day = e$day[nrow(e)],
        n_days = nrow(e),
        spill_max = safe_max(e$spillway_discharge_m3s),
        spill_total_mcm = sum(e$spillway_discharge_m3s, na.rm = TRUE) * 86400 / 1e6,
        inflow_mean = safe_mean(e$inflow_m3s),
        wl_max = safe_max(e$upstream_water_level_m),
        stringsAsFactors = FALSE)
    }
  }
  spill_df <- if (length(spill_events)) do.call(rbind, spill_events) else
    data.frame(reservoir = character(0), start_day = character(0),
               end_day = character(0), n_days = integer(0),
               spill_max = numeric(0), spill_total_mcm = numeric(0),
               inflow_mean = numeric(0), wl_max = numeric(0),
               stringsAsFactors = FALSE)
  spill_df <- spill_df[order(spill_df$reservoir, spill_df$start_day), , drop = FALSE]
  rownames(spill_df) <- NULL

  write_csv_df(res_by_res,    file.path(OUT, "e8_reservoir_summary.csv"))
  write_csv_df(res_by_season, file.path(OUT, "e8_reservoir_by_season.csv"))
  write_csv_df(res_by_year,   file.path(OUT, "e8_reservoir_by_year.csv"))
  write_csv_df(spill_df,      file.path(OUT, "e8_reservoir_spill_events.csv"))
  cat("[E8] ho chua:", length(unique(res$reservoir)), "ho |",
      nrow(spill_df), "dot xa tran\n")

  save_fig({
    ggplot(res_by_season, aes(x = reservoir, y = inflow_mean, fill = season)) +
      geom_col(position = position_dodge(width = 0.8)) +
      labs(title = "Luu luong vao ho chua theo mua",
           subtitle = "Mua mua (5-10) va mua kho (11-4)",
           x = "Ho chua", y = "Luong vao TB (m3/s)", fill = "Mua") +
      theme_pgnn()
  }, file.path(OUT, "e8_reservoir_inflow_by_season.png"), width = 1000, height = 620)

  save_fig({
    ggplot(res_by_res, aes(x = reorder(reservoir, inflow_mean), y = inflow_mean)) +
      geom_col(fill = palette_pgnn1(2)) +
      geom_point(aes(y = inflow_max), color = palette_pgnn1(1), size = 1.8) +
      coord_flip() +
      labs(title = "Luu luong vao TB va lon nhat theo ho chua",
           subtitle = "Cot = trung binh, cham = gia tri lon nhat",
           x = "Ho chua", y = "Luong vao (m3/s)") +
      theme_pgnn()
  }, file.path(OUT, "e8_reservoir_inflow.png"), width = 1000, height = 620)
}

# ---------------------------------------------------------------------------
# 5. MUA: LUONG MUA NGAY, THEO MUA
# ---------------------------------------------------------------------------
rain <- load_rainfall()
if (nrow(rain)) {
  rain <- rain[!is.na(rain$rainfall_mm), , drop = FALSE]
  rain$year <- as.integer(substr(rain$day, 1, 4))
  rain$month <- as.integer(substr(rain$day, 6, 7))
  rain$season <- season_of_month(rain$month)

  rain_by_station <- rain %>%
    group_by(station) %>%
    summarise(
      n = n(), n_days = n_distinct(day),
      rain_mean = safe_mean(rainfall_mm), rain_median = median(rainfall_mm),
      rain_max = safe_max(rainfall_mm), rain_sd = sd(rainfall_mm),
      rain_p95 = quantile(rainfall_mm, 0.95, na.rm = TRUE),
      n_wet_days = sum(rainfall_mm >= 1, na.rm = TRUE),
      .groups = "drop")

  rain_by_season <- rain %>%
    group_by(station, season) %>%
    summarise(n = n(), rain_mean = safe_mean(rainfall_mm),
              rain_max = safe_max(rainfall_mm),
              rain_p95 = quantile(rainfall_mm, 0.95, na.rm = TRUE),
              .groups = "drop")

  # Luong mua thang (tong) theo tram -> de ve bieu do chuoi thoi gian
  rain_monthly <- rain %>%
    group_by(station, year, month) %>%
    summarise(rain_total = sum(rainfall_mm), n_days = n(), .groups = "drop") %>%
    mutate(date = as.Date(sprintf("%04d-%02d-01", year, month))) %>%
    arrange(station, date)

  write_csv_df(rain_by_station, file.path(OUT, "e8_rainfall_by_station.csv"))
  write_csv_df(rain_by_season,  file.path(OUT, "e8_rainfall_by_season.csv"))
  write_csv_df(rain_monthly,     file.path(OUT, "e8_rainfall_monthly.csv"))
  cat("[E8] tram mua:", length(unique(rain$station)), "| so dong mua:", nrow(rain), "\n")

  save_fig({
    ggplot(rain_by_season, aes(x = station, y = rain_mean, fill = season)) +
      geom_col(position = position_dodge(width = 0.8)) +
      labs(title = "Luong mua ngay TB theo tram va theo mua",
           subtitle = "Mua mua (5-10) va mua kho (11-4)",
           x = "Tram mua", y = "Luong mua TB (mm/ngay)", fill = "Mua") +
      theme_pgnn() +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  }, file.path(OUT, "e8_rainfall_by_season.png"), width = 1200, height = 700)
}

# ---------------------------------------------------------------------------
# 6. XU HUONG: MANN-KENDALL + DO DOC SEN (PHU AN, NHA BE)
# ---------------------------------------------------------------------------
# Dung goi `trend` (da co san trong `.rlib`):
#   - `mk.test(x)` -> Mann-Kendall (Z, p-value, Sen's slope)
#   - `sen.slope(x)` -> do docc Sen (m/nam)
# Bien phu thuoc: dung DINH THU NHAT (dinh trieu) de phan anh bien do thuy trieu.
trend_stations <- c("PHU_AN", "NHA_BE")
mk_rows <- list()
for (s in trend_stations) {
  d <- all_pk[all_pk$station == s & all_pk$kind == "peak1", , drop = FALSE]
  d <- d[!is.na(d$h) & !is.na(d$year), , drop = FALSE]
  if (!nrow(d)) next
  # gom theo NAM (trung binh dinh trong nam) -> chuoi thoi gian don
  byy <- d %>% group_by(year) %>%
    summarise(h = mean(h), .groups = "drop") %>%
    arrange(year)
  x <- byy$h
  if (length(x) < 8 || stats::sd(x) == 0) next
  mk <- tryCatch(trend::mk.test(x), error = function(e) NULL)
  # `trend::sens.slope()` (Sen's slope) tra ve doi tuong `htest`:
  #   $estimates  : vector co ten ("Sen's slope")
  #   $p.value    : xac suat
  #   $conf.int   : khoang tin cay 95% cho do docc
  sen <- tryCatch(trend::sens.slope(x), error = function(e) NULL)
  sen_slope <- if (is.null(sen)) NA_real_ else unname(sen$estimates[1])
  sen_p     <- if (is.null(sen)) NA_real_ else sen$p.value
  sen_lo    <- if (is.null(sen)) NA_real_ else sen$conf.int[1]
  sen_hi    <- if (is.null(sen)) NA_real_ else sen$conf.int[2]
  mk_rows[[length(mk_rows) + 1L]] <- data.frame(
    station = s, n_years = length(x),
    year_first = byy$year[1], year_last = byy$year[nrow(byy)],
    h_first = x[1], h_last = x[length(x)],
    mk_Z = if (is.null(mk)) NA_real_ else unname(mk$statistic),
    mk_p = if (is.null(mk)) NA_real_ else mk$p.value,
    trend = if (is.null(mk)) NA_character_ else
      if (mk$p.value < 0.05) ifelse(unname(mk$statistic) > 0, "Tang", "Giam") else "Khong xu huong",
    sen_slope = sen_slope, sen_p = sen_p,
    sen_ci_lo = sen_lo, sen_ci_hi = sen_hi,
    stringsAsFactors = FALSE)
  # luu chuoi de ve hinh
  assign(paste0("trend_", s), byy)
}
mk_df <- if (length(mk_rows)) do.call(rbind, mk_rows) else
  data.frame(station = character(0), stringsAsFactors = FALSE)
write_csv_df(mk_df, file.path(OUT, "e8_trend_mk_sen.csv"))
cat("[E8] Mann-Kendall / Sen:", nrow(mk_df), "tram\n")

for (s in trend_stations) {
  v <- paste0("trend_", s)
  if (!exists(v, inherits = FALSE)) next
  byy <- get(v)
  p <- ggplot(byy, aes(x = year, y = h)) +
    geom_line(color = palette_pgnn1(1), linewidth = 0.7) +
    geom_point(size = 2) +
    labs(title = paste0("Xu huong dinh trieu tai ", s),
         subtitle = "Diem = trung binh dinh thu nhat trong nam",
         x = "Nam", y = "Do cao dinh TB (m)") +
    theme_pgnn()
  r <- mk_df[mk_df$station == s, , drop = FALSE]
  if (nrow(r)) {
    p <- p + ggplot2::annotate("text", x = max(byy$year), y = max(byy$h),
      label = sprintf("Sen = %.4f m/nam\nMK p = %.4g (%s)",
                      r$sen_slope, r$mk_p, r$trend),
      hjust = 1, vjust = 1, size = 3.2)
  }
  save_fig(p, file.path(OUT, paste0("e8_trend_", tolower(s), ".png")),
           width = 900, height = 600)
}

# ---------------------------------------------------------------------------
# 7. PHAN PHOI THUOC TINH MERIT TRONG CAY 44045960
# ---------------------------------------------------------------------------
# `merit_river_reaches`: thuoc tinh thuy trieu cua tung doan song (COMID).
# Cay 44045960 = luu vuc goc. Doi voi moi doan: do dai, doc dong, luu vuc,
#   he so song, va bac (order) -> phan bo.
mr <- mongo_agg("merit_river_reaches",
                 paste0('[{"$project":{"_id":0,"COMID":1,"pfaf2":1,"lengthkm":1,',
                        '"lengthdir":1,"sinuosity":1,"slope":1,"uparea":1,"order":1,',
                        '"strmDrop_t":1,"maxup":1,"NextDownID":1,',
                        '"source_dataset":1}}]'))
if (nrow(mr)) {
  mr$COMID <- suppressWarnings(as.numeric(mr$COMID))
  mr$pfaf2 <- suppressWarnings(as.numeric(mr$pfaf2))
  mr$lengthkm <- suppressWarnings(as.numeric(mr$lengthkm))
  mr$lengthdir <- suppressWarnings(as.numeric(mr$lengthdir))
  mr$sinuosity <- suppressWarnings(as.numeric(mr$sinuosity))
  mr$slope <- suppressWarnings(as.numeric(mr$slope))
  mr$uparea <- suppressWarnings(as.numeric(mr$uparea))
  mr$order <- suppressWarnings(as.numeric(mr$order))
  mr$strmDrop_t <- suppressWarnings(as.numeric(mr$strmDrop_t))
  mr$maxup <- suppressWarnings(as.numeric(mr$maxup))
  mr$NextDownID <- suppressWarnings(as.numeric(mr$NextDownID))

  # ---------------------------------------------------------------------------
  # Loc doan thuoc CAY 44045960.
  #
  # `merit_river_reaches` la do THUONG MOT LUOC (flat), khong phai cay long nhau:
  # moi dong la 1 doan song voi `COMID` (khoa) va `NextDownID` (doan phia duoi).
  # Do 44045960 la MOC GOC cua luu vuc, cac doan thuoc cay la doan ma chuoi
  # `NextDownID` cuoi cung dan TOI 44045960.
  #
  # THUAT TOAN: di ngược (upstream) -- bat dau tu 44045960, o moi buoc tim TAT CA
  # doan co `NextDownID` bang mot COMID dang co trong frontier, roi them vao
  # frontier. Lap lai den khi khong con doan moi (hoac het 100 vong).
  # Ket qua = tap doan trong cay (khong tinh doan goc neu trong du lieu).
  # ---------------------------------------------------------------------------
  root_comid <- 44045960
  mr <- mr[!is.na(mr$COMID), , drop = FALSE]
  # BFS vectorized: `NextDownID` tro toi doan CHA (phia duoi). Doan thuoc luu vuc
  # goc 44045960 la doan ma chuoi `NextDownID` dan TOI goc -> di nguoc dong.
  seen <- root_comid
  for (step in seq_len(200)) {
    ch <- which(!is.na(mr$NextDownID) & mr$NextDownID %in% seen &
                  !mr$COMID %in% seen)
    if (!length(ch)) break
    seen <- c(seen, mr$COMID[ch])
  }
  tree <- mr[mr$COMID %in% seen & mr$COMID != root_comid, , drop = FALSE]
  cat("[E8] MERIT cay", root_comid, ":", nrow(tree), "doan song (trong toan bo",
      nrow(mr), "doan)\n")
  if (!nrow(tree)) {
    cat("[E8] !! Khong tim thay doan song nao thuoc cay", root_comid, "\n")
  }
  cat("[E8] cot MERIT:", paste(names(tree), collapse = ", "), "\n")

  merit_summary <- data.frame(
    attribute = c("lengthkm", "lengthdir", "sinuosity", "slope", "uparea", "order"),
    n = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
               function(a) sum(!is.na(tree[[a]]))),
    mean = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                  function(a) mean(tree[[a]], na.rm = TRUE)),
    sd = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                function(a) stats::sd(tree[[a]], na.rm = TRUE)),
    min = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                 function(a) min(tree[[a]], na.rm = TRUE)),
    p25 = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                 function(a) quantile(tree[[a]], 0.25, na.rm = TRUE)),
    median = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                    function(a) median(tree[[a]], na.rm = TRUE)),
    p75 = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                 function(a) quantile(tree[[a]], 0.75, na.rm = TRUE)),
    max = sapply(c("lengthkm","lengthdir","sinuosity","slope","uparea","order"),
                 function(a) max(tree[[a]], na.rm = TRUE)),
    stringsAsFactors = FALSE)

  merit_by_order <- tree %>%
    filter(!is.na(order)) %>%
    group_by(order) %>%
    summarise(n = n(), lengthkm_mean = mean(lengthkm, na.rm = TRUE),
              lengthkm_total = sum(lengthkm, na.rm = TRUE),
              uparea_mean = mean(uparea, na.rm = TRUE),
              slope_mean = mean(slope, na.rm = TRUE),
              sinuosity_mean = mean(sinuosity, na.rm = TRUE),
              .groups = "drop")

  write_csv_df(merit_summary, file.path(OUT, "e8_merit_tree_summary.csv"))
  write_csv_df(merit_by_order, file.path(OUT, "e8_merit_by_order.csv"))
  write_csv_df(tree[, c("COMID","pfaf2","lengthkm","lengthdir","sinuosity","slope",
                         "uparea","order","strmDrop_t","maxup","NextDownID")],
               file.path(OUT, "e8_merit_tree_reaches.csv"))

  save_fig({
    ggplot(merit_by_order, aes(x = factor(order), y = lengthkm_total)) +
      geom_col(fill = palette_pgnn1(3)) +
      labs(title = "Tong dai doan song theo bac trong cay MERIT 44045960",
           subtitle = "Tong do dai (km) cua cac doan cung bac",
           x = "Bac (order)", y = "Tong do dai (km)") +
      theme_pgnn()
  }, file.path(OUT, "e8_merit_by_order.png"), width = 900, height = 600)

  save_fig({
    d <- tree[!is.na(tree$lengthkm) & !is.na(tree$uparea), ]
    ggplot(d, aes(x = uparea, y = lengthkm)) +
      geom_point(color = palette_pgnn1(4), alpha = 0.55, size = 1.4) +
      labs(title = "Quan he luu vuc - do dai doan song (cay 44045960)",
           subtitle = sprintf("n = %d doan", nrow(d)),
           x = "Luu vuc thuoc (km2)", y = "Dai doan song (km)") +
      theme_pgnn()
  }, file.path(OUT, "e8_merit_uparea_vs_length.png"), width = 900, height = 620)
}

# ---------------------------------------------------------------------------
# 8. NGOAI LEI: IQR / MAD tren dinh trieu theo tram-thang
# ---------------------------------------------------------------------------
# Voi moi (tram, thang): tinh IQR = q3 - q1 va MAD = median(|x - median|).
# Ngưỡng ngoai l:
#   IQR : ngoai l tren (q3 + 1.5*IQR) hoac duoi (q1 - 1.5*IQR)
#   MAD : ngoai le tren (median + 3*1.4826*MAD) hoac duoi (median - 3*1.4826*MAD)
# Dinh bi loai dong oi: giu lai dinh cua BAN TIN (khong sua du lieu), chi DANH DAU.
out_rows <- list()
outl_rows <- list()
for (s in unique(all_pk$station)) {
  d <- all_pk[all_pk$station == s, , drop = FALSE]
  for (m in sort(unique(d$month))) {
    v <- d$h[d$month == m]
    v <- v[!is.na(v)]
    if (length(v) < 8) next
    q1 <- quantile(v, 0.25); q3 <- quantile(v, 0.75)
    iqr <- q3 - q1
    med <- median(v)
    mad <- median(abs(v - med))
    # ngưỡng MAD (bien doi chuan 1.4826 cho chuan hoa)
    mad_s <- 1.4826 * mad
    lo_iqr <- q1 - 1.5 * iqr; hi_iqr <- q3 + 1.5 * iqr
    lo_mad <- if (mad_s > 0) med - 3 * mad_s else NA_real_
    hi_mad <- if (mad_s > 0) med + 3 * mad_s else NA_real_
    out_rows[[length(out_rows) + 1L]] <- data.frame(
      station = s, month = m, n = length(v),
      mean = mean(v), median = med, sd = stats::sd(v),
      q1 = q1, q3 = q3, iqr = iqr, mad = mad, mad_scaled = mad_s,
      lo_iqr = lo_iqr, hi_iqr = hi_iqr,
      lo_mad = lo_mad, hi_mad = hi_mad,
      n_out_iqr = sum(v < lo_iqr | v > hi_iqr),
      n_out_mad = if (is.na(lo_mad)) NA_integer_ else sum(v < lo_mad | v > hi_mad),
      stringsAsFactors = FALSE)
    # danh sach dinh bi danh dau ngoai l
    dd <- d[d$month == m, , drop = FALSE]
    dd <- dd[!is.na(dd$h), , drop = FALSE]
    bad <- (dd$h < lo_iqr) | (dd$h > hi_iqr)
    bad_mad <- if (!is.na(lo_mad)) ((dd$h < lo_mad) | (dd$h > hi_mad)) else rep(FALSE, nrow(dd))
    if (any(bad | bad_mad)) {
      outl_rows[[length(outl_rows) + 1L]] <- data.frame(
        station = s, month = m, day = dd$day[bad | bad_mad],
        kind = dd$kind[bad | bad_mad], h = dd$h[bad | bad_mad],
        t_h = dd$t_h[bad | bad_mad],
        flag_iqr = bad[bad | bad_mad], flag_mad = bad_mad[bad | bad_mad],
        median = med, lo_iqr = lo_iqr, hi_iqr = hi_iqr,
        lo_mad = lo_mad, hi_mad = hi_mad,
        stringsAsFactors = FALSE)
    }
  }
}
out_df <- if (length(out_rows)) do.call(rbind, out_rows) else
  data.frame(station = character(0), stringsAsFactors = FALSE)
outl_df <- if (length(outl_rows)) do.call(rbind, outl_rows) else
  data.frame(station = character(0), stringsAsFactors = FALSE)
if (nrow(outl_df)) {
  outl_df$flag_iqr[is.na(outl_df$flag_iqr)] <- FALSE
  outl_df$flag_mad[is.na(outl_df$flag_mad)] <- FALSE
  outl_df <- outl_df[order(outl_df$station, outl_df$month, outl_df$day), , drop = FALSE]
  rownames(outl_df) <- NULL
}
write_csv_df(out_df,  file.path(OUT, "e8_outlier_thresholds_by_station_month.csv"))
write_csv_df(outl_df, file.path(OUT, "e8_outlier_tidal_peaks.csv"))
cat("[E8] ngoai lei: ", nrow(out_df), "nhom tram-thang |", nrow(outl_df),
    "dinh bi danh dau\n")

save_fig({
  d <- if ("n_out_iqr" %in% names(out_df))
    out_df[!is.na(out_df$n_out_iqr) & out_df$n_out_iqr > 0, , drop = FALSE] else
    out_df[0, , drop = FALSE]
  if (!nrow(d)) {
    ggplot(d, aes(x = station)) + labs(title = "Khong co dinh ngoai lei (IQR 1.5)")
  } else {
    ggplot(d, aes(x = station, y = n_out_iqr)) +
      geom_col(fill = palette_pgnn1(2)) +
      labs(title = "So dinh bi danh dau la ngoai lei theo tram",
           subtitle = "Nguong IQR = 1.5*IRQ, MAD = 3*1.4826*MAD",
           x = "Tram", y = "So dinh ngoai l") +
      theme_pgnn() +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
  }
}, file.path(OUT, "e8_outlier_counts.png"), width = 1000, height = 620)

cat("[E8] Xong. Ket qua:\n")
cat(paste0("  - ", basename(list.files(OUT)), collapse = "\n  - "), "\n")
