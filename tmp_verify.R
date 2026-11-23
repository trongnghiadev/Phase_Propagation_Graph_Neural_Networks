.libPaths(c(".rlib", .libPaths()))
suppressPackageStartupMessages(library(dplyr))

ok <- function(label, cond, got, want) {
  cat(sprintf("[%s] %-52s got=%-22s want=%s\n",
              if (isTRUE(cond)) "OK" else "FAIL", label,
              paste(as.character(got), collapse=","), paste(as.character(want), collapse=",")))
}

rd <- function(p) read.csv(p, stringsAsFactors = FALSE)

# --- 4.1 E8 ---
bs <- rd("results/E8/e8_peak_summary_by_station.csv")
ok("E8 sum n_dinhs", sum(bs$n_dinhs) == 60591, sum(bs$n_dinhs), 60591)
ok("E8 n_dinhs range", all(c(min(bs$n_dinhs)==6489, max(bs$n_dinhs)==6868)),
   paste(range(bs$n_dinhs), collapse="-"), "6489-6868")
ok("E8 n_dinhs <= 32day*2/station", all(bs$n_dinhs <= bs$n_days*2), "ok", "ok")

fs <- rd("results/E8/e8_flood_season_profile.csv")
top3 <- fs[order(-fs$h_median), ][1:3, ]
ok("E8 flood season months", identical(as.integer(top3$month), c(10L,11L,12L)),
   paste(top3$month, collapse=","), "10,11,12")
ok("E8 flood medians", all(abs(top3$h_median - c(1.43,1.42,1.34)) < 0.006),
   paste(round(top3$h_median,3), collapse=","), "1.43,1.42,1.34")
ok("E8 month6 min", fs$h_median[fs$month==6] < min(fs$h_median),
   fs$h_median[fs$month==6], "min")

vt <- bs[bs$station=="VUNG_TAU", ]
ok("E8 VungTau mean/median", all(abs(c(vt$h_mean, vt$h_median) - c(3.65,3.68)) < 0.006),
   paste(round(c(vt$h_mean,vt$h_median),3),collapse=","), "3.65,3.68")
bh <- bs[bs$station=="BIEN_HOA", ]
ok("E8 BienHoa mean/median", all(abs(c(bh$h_mean, bh$h_median) - c(1.39,1.43)) < 0.006),
   paste(round(c(bh$h_mean,bh$h_median),3),collapse=","), "1.39,1.43")

af <- rd("results/E8/e8_alarm_frequency_by_year.csv")
ok("E8 alarm rows", nrow(af) == 22, nrow(af), 22)

rs <- rd("results/E8/e8_reservoir_summary.csv")
ok("E8 reservoirs", nrow(rs) == 6, nrow(rs), 6)
ok("E8 spill days Tri An", rs$n_spill_days[rs$reservoir=="RES_TRI_AN"] == 322,
   rs$n_spill_days[rs$reservoir=="RES_TRI_AN"], 322)
ok("E8 spill days SRPM", rs$n_spill_days[rs$reservoir=="RES_SR_P_MIENG"] == 548,
   rs$n_spill_days[rs$reservoir=="RES_SR_P_MIENG"], 548)

mt <- rd("results/E8/e8_merit_tree_summary.csv")
ok("E8 merit n", all(mt$n == 1072), unique(mt$n), 1072)
ok("E8 merit lengthkm mean", abs(mt$mean[mt$attribute=="lengthkm"] - 8.68) < 0.01,
   mt$mean[mt$attribute=="lengthkm"], 8.68)
ok("E8 merit uparea mean", abs(mt$mean[mt$attribute=="uparea"] - 1454.8) < 0.1,
   mt$mean[mt$attribute=="uparea"], 1454.8)
ok("E8 merit order mean", abs(mt$mean[mt$attribute=="order"] - 1.96) < 0.01,
   mt$mean[mt$attribute=="order"], 1.96)

tr <- rd("results/E8/e8_trend_mk_sen.csv")
ok("E8 trend 2 stations", nrow(tr)==2, nrow(tr), 2)
ok("E8 trend both not significant", all(tr$trend == "Khong xu huong"), tr$trend, "Khong xu huong")
ok("E8 trend PA sen", abs(tr$sen_slope[tr$station=="PHU_AN"] + 0.0045318) < 1e-5,
   tr$sen_slope[tr$station=="PHU_AN"], -0.0045318)

out <- rd("results/E8/e8_outlier_tidal_peaks.csv")
ok("E8 outliers", nrow(out) == 2514, nrow(out), 2514)
th <- rd("results/E8/e8_outlier_thresholds_by_station_month.csv")
ok("E8 outlier groups", nrow(th) == 108, nrow(th), 108)

ra <- rd("results/E8/e8_rainfall_by_station.csv")
ok("E8 rainfall rows", nrow(ra) == 10, nrow(ra), 10)

# --- 4.2 E1 harmonic ---
hf <- rd("results/E1/harmonic_fit_summary.csv")
ok("E1 stations fit", nrow(hf) == 9, nrow(hf), 9)
ok("E1 n_obs range", all(c(min(hf$n_obs)==13002, max(hf$n_obs)==13747)),
   paste(range(hf$n_obs),collapse="-"), "13002-13747")
ok("E1 VT a0", abs(hf$a0_m[hf$station=="VUNG_TAU"] - 2.581) < 0.001,
   hf$a0_m[hf$station=="VUNG_TAU"], 2.581)
ok("E1 BL a0", abs(hf$a0_m[hf$station=="BEN_LUC"] + 0.2184) < 0.001,
   hf$a0_m[hf$station=="BEN_LUC"], -0.2184)
ok("E1 var_expl VT", abs(hf$var_expl[hf$station=="VUNG_TAU"] - 0.949) < 0.001,
   hf$var_expl[hf$station=="VUNG_TAU"], 0.949)

hv <- rd("results/E1/harmonic_validation.csv")
ok("E1 val MAE h range", all(c(min(hv$mae_height_m) > 0.213, max(hv$mae_height_m) < 0.417)),
   paste(round(range(hv$mae_height_m),3),collapse="-"), "0.213-0.416")
ok("E1 val MAE hour range", all(c(min(hv$mae_peak_hour_h) > 0.706, max(hv$mae_peak_hour_h) < 1.507)),
   paste(round(range(hv$mae_peak_hour_h),3),collapse="-"), "0.706-1.506")

hc <- rd("results/E1/harmonic_components.csv")
ok("E1 components 9x11", nrow(hc) == 99, nrow(hc), 99)

at <- rd("data/processed/features/astro_tide.csv")
ok("E1 astro_tide rows", nrow(at) == 3607, nrow(at), 3607)
ok("E1 astro_tide cols", identical(names(at), c("day","h1","h2","t1_h","t2_h")),
   paste(names(at),collapse=","), "day,h1,h2,t1_h,t2_h")
ok("E1 astro_tide range", as.character(min(at$day))=="2016-12-31" && as.character(max(at$day))=="2026-11-15",
   paste(min(at$day),max(at$day)), "2016-12-31 2026-11-15")

ats <- rd("results/E1/astro_tide_by_station.csv")
ok("E1 astro_tide_by_station rows", nrow(ats) == 32463, nrow(ats), 32463)
ar <- rd("results/E1/astro_residual.csv")
ok("E1 astro_residual rows", nrow(ar) == 60573, nrow(ar), 60573)

# --- 4.3 phase lag ---
pl <- rd("results/E1/phase_lag_by_station.csv")
ok("E1 phase_lag rows", nrow(pl) == 16, nrow(pl), 16)
p1 <- pl[pl$peak_type==1, ]
ok("E1 lag stations 8", nrow(p1)==8, nrow(p1), 8)
ok("E1 NHA_BE lag", abs(p1$lag_median_h[p1$station=="NHA_BE"] - 1.7331) < 0.001,
   p1$lag_median_h[p1$station=="NHA_BE"], 1.7331)
ok("E1 DAU_TIENG lag", abs(p1$lag_median_h[p1$station=="DAU_TIENG"] - 10.3613) < 0.001,
   p1$lag_median_h[p1$station=="DAU_TIENG"], 10.3613)
ok("E1 IQR NHA_BE", abs(p1$lag_iqr_h[p1$station=="NHA_BE"] - 0.0258) < 0.001,
   p1$lag_iqr_h[p1$station=="NHA_BE"], 0.0258)
ok("E1 boot n=2000", all(pl$n_boot==2000), unique(pl$n_boot), 2000)

h5 <- rd("results/E1/h5_lag_vs_distance.csv")
h5a <- h5[h5$model=="lag_median_h ~ dist_to_root_km", ]
ok("H5 slope", abs(h5a$slope - 0.0532) < 0.0001, h5a$slope, 0.0532)
ok("H5 R2", abs(h5a$r_squared - 0.9417) < 0.0001, h5a$r_squared, 0.9417)
ok("H5 p", abs(h5a$pearson_p - 6.34e-05) < 1e-7, h5a$pearson_p, 6.34e-05)
h5b <- h5[h5$model=="lag_h_harmonic ~ dist_to_root_km", ]
ok("H5 harmonic R2", abs(h5b$r_squared - 0.2084) < 0.0001, h5b$r_squared, 0.2084)

h6 <- rd("results/E1/h6_lag_vs_discharge.csv")
ok("H6 n", all(h6$n==12933), unique(h6$n), 12933)
s1 <- h6[h6$formula=="lag_day_h ~ q_release + season" & h6$term=="seasonMua mua", ]
ok("H6 season +0.594", abs(s1$estimate - 0.5944) < 0.001, s1$estimate, 0.5944)
ok("H6 season p", abs(s1$p_value - 6.339e-12) < 1e-14, s1$p_value, 6.339e-12)

h7 <- rd("results/E1/h7_amplitude_ratio_fit.csv")
ok("H7 slope", abs(h7$slope - (-0.5914)) < 0.001, h7$slope, -0.5914)
ok("H7 R2", abs(h7$r_squared - 0.3975) < 0.001, h7$r_squared, 0.3975)

cc <- rd("results/E1/crosscorr_release_residual_best.csv")
ok("crosscorr rows", nrow(cc)==6, nrow(cc), 6)
ok("crosscorr max r", abs(max(cc$r) - 0.2296) < 0.001, max(cc$r), 0.2296)

# --- 4.4 E5 ---
ev <- rd("results/E5/evaluation_overall.csv")
ok("E5 models", nrow(ev)==3, nrow(ev), 3)
kt <- ev[ev$model=="B_KTTV", ]
ok("E5 KTTV n", kt$n==14287, kt$n, 14287)
ok("E5 KTTV MAE", abs(kt$mae-0.1271)<0.0001, kt$mae, 0.1271)
ok("E5 KTTV MAE_t", abs(kt$mae_t_h-5.1916)<0.001, kt$mae_t_h, 5.1916)
b0 <- ev[ev$model=="B0", ]
ok("E5 B0 n", b0$n==60573, b0$n, 60573)
ok("E5 B0 NSE", abs(b0$nse-0.9247)<0.001, b0$nse, 0.9247)
bh <- ev[ev$model=="B_harm", ]
ok("E5 B_harm n", bh$n==11900, bh$n, 11900)
ok("E5 B_harm MAE_t", abs(bh$mae_t_h-4.8020)<0.001, bh$mae_t_h, 4.8020)

cg <- rd("results/E5/evaluation_contingency.csv")
k1 <- cg[cg$model=="B_KTTV" & cg$threshold=="I", ]
ok("E5 CSI_I KTTV", abs(k1$CSI-0.5502)<0.001, k1$CSI, 0.5502)
ok("E5 POD_I KTTV", abs(k1$POD-0.6235)<0.001, k1$POD, 0.6235)
b0c <- cg[cg$model=="B0" & cg$threshold=="I", ]
ok("E5 CSI_I B0", abs(b0c$CSI-0.3224)<0.001, b0c$CSI, 0.3224)

wl <- rd("results/E5/evaluation_wilcoxon.csv")
ok("E5 wilcoxon 3 pairs", nrow(wl)==3, nrow(wl), 3)
dm <- rd("results/E5/evaluation_diebold_mariano.csv")
ok("E5 DM 3 pairs", nrow(dm)==3, nrow(dm), 3)
ok("E5 all holm sig", all(wl$p_holm < 0.05) && all(dm$p_holm < 0.05), "all<0.05", "all<0.05")

bl <- rd("results/E5/evaluation_by_lead.csv")
ok("E5 by_lead rows", nrow(bl)==7, nrow(bl), 7)

ms <- rd("results/E5/evaluation_matched_sample.csv")
ok("E5 matched rows", nrow(ms)==86760, nrow(ms), 86760)

cat("\n=== HET ===\n")
