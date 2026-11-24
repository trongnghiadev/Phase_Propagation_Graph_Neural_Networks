# R/harmonics.R -- Phan tich dieu hoa thuy trieu (Chuong 4, muc 4.3).
#
# ---------------------------------------------------------------------------
# PHUONG PHAP: BOC NHIEU BAY PHUONG NHO GOC (LSQ) + BO SUNG THIEN VAN
# ---------------------------------------------------------------------------
# LY DO CHON PHUONG PHAP (ghi ro trong README va trong report):
#
#  1) Du lieu `tide_extremes` LA DINH/CHAN (extreme), KHONG phai chuoi muc thuy
#     trong ngay: moi nguon chi co ~2 dinh + 2 chan/ngay, va cac moc thoi gian
#     KHONG deu (phu thuoc gio quan trong cua bang tin, khong phai boi phan
#     deu cua mau thoi gian). Voi vay:
#       - KHONG dung phuong phap phan tich Fourier tren chuoi deu (spectral
#         analysis / FFT) -- phuong phap nay dua vao gia dinh mau deu va se
#         tao "aliasing" khi dung;
#       - KHONG dung `TideHarmonics::harm()` (goi nay dung "least squares" voi
#         vector trong "harmonic space" cho chuoi deu hoac de suy do duoc)
#         -- va goi `oce` se can GDAL/PROJ khong co san trong moi truong nay.
#
#  2) Voi du lieu khong deu, phuong phap chuan la phan tich dieu hoa bang
#     **BOC NHIEU BAY PHUONG NHO GOC (Least Squares Harmonic Analysis, LSQHA)**:
#     mo hinh hoa trieu:
#         h(t) = a0 + SUM_j [ a_j cos(w_j t) + b_j sin(w_j t) ]  (+ xu huong tuy duong)
#     va giai bang **phuong phap binh phuong nho nhat** (linear least squares)
#     tren ma tran truc giao cua cac thanh phan dieu hoa. Day la phuong phap:
#       - khong doi ve mau thoi gian deu (nhanh cac moc o du khong deu);
#       - giu nguyen thong tin bien do va pha cua tung thanh phan;
#       - co the them thanh phan "khac" (nhiu hat) de bai chung nham cao.
#
#  3) Cac thanh phan dieu hoa chinh cua trieu bien (theo Godin (1968),
#     *Tidal Hydrodynamics*, va Foreman (1977), *Manual for Tidal Heights Analysis*):
#       M2  = 12.4206012 h  (nuoc lon)         S2  = 12.0000000 h (nuoc nho)
#       N2  = 12.6583475 h  (nuoc lon)         K1  = 23.9344695 h (tu bien)
#       O1  = 25.8193387 h  (tu bien)          P1  = 24.0658877 h (tu bien)
#       Q1  = 26.8683500 h (tu bien)           M4  = 6.2106015 h  (song bien)
#       MS4 = 6.1033390 h (song bien)          Mf  = 327.8599387 h (bien)
#       M6  = 4.1402011 h                      MK3 = 8.1771399 h
#
#  4) Cac thanh phan bien (Mf) va bien dai (SSA) bi LOAI BO mac dinh khi
#     sinh trieu du tinh cho model: bien do nho va gay nhieu doi so -> phu thuoc
#     vao so do va lam nhieu dot sai. Cac thanh phan dung cho "bang chung"
#     (K1, O1, M2, S2, N2, P1, Q1, M4, MS4, M6, MK3) duoc giu de bao cao.
#
#  5) XU HUONG TUY DUONG: cong them 1 bien tuyen tinh trong mo hinh de thu hien
#     "dinh" (datum) khac nhau giua cac moc, va giu mo hinh bam sat duong co so.
#
# ---------------------------------------------------------------------------
# DOI CHIEU GIO: moc thoi gian trong mo hinh la gio Viet Nam (UTC+7). Moc `t`
# duoc do bang SO GIAY (giay) ke tu moc goc bat bien (xem `t_ref()`).
# Moc goc chon = 0.0 giay Viet Nam ngay 2000-01-01 00:00:00.

HARMONIC_COMPONENTS <- c("M2", "S2", "N2", "K1", "O1", "P1", "Q1",
                         "M4", "MS4", "M6", "MK3")

# Chu ky (gio) cua tung thanh phan.
HARMONIC_PERIOD_H <- c(M2 = 12.4206012, S2 = 12.0000000, N2 = 12.6583475,
                       K1 = 23.9344695, O1 = 25.8193387, P1 = 24.0658877,
                       Q1 = 26.8683500, M4 = 6.2106015, MS4 = 6.1033390,
                       M6 = 4.1402011, MK3 = 8.1771399)

# Mo hieu (rad/s) cho moi thanh phan.
HARMONIC_OMEGA <- 2 * pi / (HARMONIC_PERIOD_H * 3600)

# Moc goc thoi gian: 2000-01-01 00:00:00 gio Viet Nam.
T0_VN <- as.POSIXct("2000-01-01 00:00:00", tz = PGNN_TZ,
                    format = "%Y-%m-%d %H:%M:%S")

# So giay tu moc goc cho mot moc thoi gian gio Viet Nam.
t_ref <- function(ts) as.numeric(difftime(ts, T0_VN, units = "secs"))

# ---------------------------------------------------------------------------
# Tao ma tran dieu hoa
# ---------------------------------------------------------------------------
# `harmonic_matrix(t, components, trend)` tra ve ma tran X (n x p) cua mo hinh:
#   cot dau = 1 (he so trung binh)
#   cac cot sau = cos(w t), sin(w t) cho tung thanh phan (theo thu tu `components`)
#   neu `trend = TRUE`: them cot (t / 1e6) cho xu huong tuyen tinh.
harmonic_matrix <- function(t, components = HARMONIC_COMPONENTS, trend = TRUE) {
  t <- as.numeric(t)
  X <- matrix(1, nrow = length(t), ncol = 1)
  for (nm in components) {
    w <- HARMONIC_OMEGA[[nm]]
    X <- cbind(X, cos(w * t), sin(w * t))
  }
  if (isTRUE(trend)) X <- cbind(X, t / 1e6)
  colnames(X) <- c("a0",
                   unlist(lapply(components, function(nm) c(paste0("cos_", nm), paste0("sin_", nm)))),
                   if (isTRUE(trend)) "trend" else NULL)
  X
}

# ---------------------------------------------------------------------------
# Fit mo hinh dieu hoa (binh phuong nho nhat)
# ---------------------------------------------------------------------------
# `fit_harmonics(t, y, components, trend)` tra ve danh sach:
#   coef      : vector he so (a0, cos_*, sin_*, trend)
#   amplitude : bien do cua tung thanh phan (m)
#   phase_deg : pha (do) cua tung thanh phan
#   period_h  : chu ky (h)
#   var_expl  : phan tram giai thich
#   rms_resid : sai so RMS (m)
# `phase_deg` theo quy uoc: h(t) = a0 + SUM A_j cos(w_j t - g_j), nen
#   g_j = atan2(b_j, a_j) (do). g_j > 0 => dinh dich phia truoc moc goc.
fit_harmonics <- function(t, y, components = HARMONIC_COMPONENTS, trend = TRUE) {
  ok <- !is.na(y) & !is.na(t)
  t <- t[ok]; y <- y[ok]
  if (length(y) < 10) stop("Khong du du lieu de fit dieu hoa (< 10 moc).", call. = FALSE)
  X <- harmonic_matrix(t, components, trend)
  cf <- qr.solve(X, y)
  res <- y - as.vector(X %*% cf)
  n <- length(y); p <- ncol(X)
  amp <- numeric(length(components)); pha <- numeric(length(components))
  for (i in seq_along(components)) {
    a <- cf[2 * i]; b <- cf[2 * i + 1]
    amp[i] <- sqrt(a^2 + b^2)
    pha[i] <- atan2(b, a) * 180 / pi
  }
  names(amp) <- components
  names(pha) <- components
  sst <- sum((y - mean(y))^2)
  sse <- sum(res^2)
  list(coef = cf, components = components,
       amplitude = amp, phase_deg = pha,
       period_h = HARMONIC_PERIOD_H[components],
       var_expl = if (sst > 0) 1 - sse / sst else NA_real_,
       rms_resid = sqrt(sse / n),
       n = n, df = n - p)
}

# ---------------------------------------------------------------------------
# Tao trieu thien van (du tinh) tai cac moc bat ky
# ---------------------------------------------------------------------------
# `astro_at(t, fit)` tra ve muc thuy du tinh (m) theo mo hinh da fit.
astro_at <- function(t, fit) {
  has_trend <- "trend" %in% names(fit$coef)
  X <- harmonic_matrix(t, fit$components, has_trend)
  as.vector(X %*% fit$coef)
}

# ---------------------------------------------------------------------------
# Tim cac dinh/chan du tinh trong mot chuoi (cho muc 4.3: tao trieu 2 dinh/ngay)
# ---------------------------------------------------------------------------
# Quet cac cuc bo toi da, dung "prominence" de loai bo nhiu roi (gia tri mong manh
# do nhieu/thay doi dong), va tra ve moc + gia tri + loai.
find_extremes_t <- function(t, y, prominence = 0.05) {
  if (length(t) < 5) return(list(t = numeric(0), y = numeric(0), kind = character(0)))
  ord <- order(t); t <- t[ord]; y <- y[ord]
  n <- length(t)
  win <- 60L
  out_t <- numeric(0); out_y <- numeric(0); out_k <- character(0)
  for (i in 2:(n - 1)) {
    ismax <- y[i] > y[i - 1] && y[i] >= y[i + 1]
    ismin <- y[i] < y[i - 1] && y[i] <= y[i + 1]
    if (!ismax && !ismin) next
    li <- max(1L, i - win):(i - 1L)
    ri <- (i + 1L):min(n, i + win)
    prom <- if (ismax) y[i] - min(min(y[li]), min(y[ri]))
             else max(max(y[li]), max(y[ri])) - y[i]
    if (!is.finite(prom) || prom < prominence) next
    out_t <- c(out_t, t[i]); out_y <- c(out_y, y[i])
    out_k <- c(out_k, if (ismax) "high" else "low")
  }
  list(t = out_t, y = out_y, kind = out_k)
}

# ---------------------------------------------------------------------------
# PHA TRONG CHU KY (GIO DE) -- dung de "khop thanh phan M2, S2, K1, O1, N2"
# ---------------------------------------------------------------------------
# Cho moc thoi gian `t` (giay) va chu ky P (giay), tra ve pha trong chu ky
# (0..2*pi) cua moc do so voi moc goc:
#   phase(t) = (omega * t) mod (2*pi)
# Moc co pha = 0 la moc goc cua thanh phan do (dinh cua duong cos).
phase_in_cycle <- function(t, period_s) (2 * pi * (t / period_s)) %% (2 * pi)

# Trung binh von Mises cua mot tap pha (rad) -> goc trong [0, 360).
mean_phase <- function(phase) {
  if (!length(phase)) return(NA_real_)
  p <- atan2(mean(sin(phase)), mean(cos(phase)))
  (p * 180 / pi) %% 360
}

# Do lech pha (degree) giua hai goc, quy ve (-180, 180].
#
# LUU Y QUAN TRONG (sua loi GĐ4): ham nay CHỈ hợp lệ khi |do tre that| < P/2,
# voi P la chu ky cua thanh phan dang xet. Neu do tre vuot nua chu ky (vd tram
# cach bien xa, do tre > 6.21 h cho M2), wrap se "ghi de" do tre bang gia tri
# am cùng bien do -> ket qua SAI DAU.
#   -> Dung `phase_diff_unwrapped()` cho uoc luong do tre thuc te.
phase_diff_deg <- function(a, b) ((a - b + 180) %% 360) - 180

# ---------------------------------------------------------------------------
# Do lech pha KHONG WRAP (dung cho uoc luong do tre)
# ---------------------------------------------------------------------------
# `phase_diff_unwrapped(a, b)` tra ve `a - b` (do) ma KHONG quy ve (-180, 180].
#
# LY DO (sua loi GĐ4 - xem README muc 4.3):
#   Do tre thuc te cua trieu bien co the vuot nua chu ky cua thanh phan dang
#   xet. Vi du DAU_TIENG cach VUNG_TAU 200 km: chenh lech pha M2 THUC TE =
#   +212.08 deg. Neu wrap ve (-180, 180] ta duoc -147.92 deg -> do tre bi
#   giam nguoc thanh tu +7.32 h thanh -5.10 h (sai dau, va khac dau voi ba
#   nguon lap doc lap: do tre thuc do +35.44 h, pha M2 +2.7 h, pha tren peak1
#   +10.36 h).
#
#   Do lai: -147.92 / (360 / 12.4206) = -5.10 h;  +212.08 / (360 / 12.4206) = +7.32 h
#
# DUNG CHI:
#   - CHI dung khi quy doi chenh lech pha -> do tre (lag) theo gio.
#   - LUON kem rang buoc `lag >= 0` (do tre trieu bien khong am: dinh o tram
#     khong the xuat hien TRUOC dinh o bien).
#   - KHONG dung de so sanh pha don le (vi du kiem tra do pha cua 2 tram) --
#     o do, wrap van dung va de doc hon.
phase_diff_unwrapped <- function(a, b) a - b

# ---------------------------------------------------------------------------
# Do tre (gio) tu chenh lech pha cua mot thanh phan dieu hoa
# ---------------------------------------------------------------------------
# `phase_lag_hours(phase_a, phase_b, period_h, wrap = FALSE)`:
#   - `phase_a`, `phase_b`: pha (do) cua thanh phan tai TRAM va tai TRAM THAM CHIEU.
#   - `period_h`: chu ky cua thanh phan (gio).
#   - `wrap = TRUE` -> quy ve (-180, 180] (chi dung khi do tre < P/2).
#   - `wrap = FALSE` (mac dinh) -> khong wrap, dung cho do tre thuc te.
#
# DAI SO QUY UOC (quan trong -- sua loi GĐ4):
#   Pha duoc dinh nghia theo `fit_harmonics()`:
#       h(t) = a0 + SUM A_j * cos(w_j * t - g_j),   g_j = atan2(b_j, a_j)
#   Do do, dinh cua thanh phan xuat hien tai moc `t` khi
#       w_j * t - g_j = 2*pi*k  =>  t = (g_j + 2*pi*k) / w_j
#   => moc dinh SOM HON (t nho hon) khi g_j LON HON.
#   Vung bien -> tram thuong nguon cham hon => dinh tai tram xuat hien CHAM HON
#   => g_tram > g_ref => chenh lech pha (g_tram - g_ref) > 0 => do tre > 0.
#
#   => `lag_h = (phase_a - phase_b) / (360 / period_h)` cho do tre DUONG khi
#      tram cham hon tham chieu. DAI SO NAY KHONG DOI.
phase_lag_hours <- function(phase_a, phase_b, period_h, wrap = FALSE) {
  d <- if (isTRUE(wrap)) phase_diff_deg(phase_a, phase_b) else phase_diff_unwrapped(phase_a, phase_b)
  d / (360 / period_h)
}
