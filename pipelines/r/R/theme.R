# R/theme.R -- Mau hinh thong nhat cho TOAN BO bao cao (Chuong 4).
#
# Dung: `library(ggplot2); theme_pgnn()` cho moi hinh, va `palette_pgnn()` de
# dung chung mau theo tinh nhat (tram / mua / he so bac).
#
# Quy tac: mot file nay la noi DUY NHAT dinh nghia font, mau, kich thuoc, vi
# khong de moi script tu dinh nghia lai (de bao cao dong nhat).

# --- Font chu ---------------------------------------------------------------
# Uu tien font co dau tieng Viet: "Arial" (Windows co san, Unicode day du).
# Neu khong co, ggplot2 se fallback ve sans-serif.
PGNN_FONT <- "Arial"

# --- Bang mau ---------------------------------------------------------------
# Bang mau phong bang mau (colorblind-friendly) + mau trung tinh cho duong.
palette_pgnn <- function(n = 6) {
  base <- c("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9",
            "#000000", "#F0E442")
  if (n <= length(base)) base[seq_len(n)] else grDevices::colorRampPalette(base)(n)
}

# MOT mau don (chuoi he xa) -- dung khi truyen cho `color =`/`fill =` cua
# geom ma khong can mapping (vi du `geom_point(color = palette_pgnn(1))`).
# LUU Y: `palette_pgnn(2)` tra ve VECTOR 2 mau -> ggplot2 bao loi
# "Aesthetics must be either length 1 or the same as the data".
palette_pgnn1 <- function(i = 1) palette_pgnn(max(1L, as.integer(i)))[1]

# Mau theo tinh nhat: bien "tram" -> mau dung de so sanh giua cac tram.
station_colors <- function(stations) {
  palette_pgnn(length(unique(stations)))[
    match(stations, unique(stations))
  ]
}

# --- Kich thuoc chu ---------------------------------------------------------
PGNN_BASE_SIZE <- 12

# --- Theme tong -------------------------------------------------------------
# Tra ve mot `ggplot2::theme()` hoan chinh dung cho bao cao.
theme_pgnn <- function(base_size = PGNN_BASE_SIZE, grid = TRUE) {
  ggplot2::theme_minimal(base_family = PGNN_FONT, base_size = base_size) +
    ggplot2::theme(
      plot.title = ggplot2::element_text(face = "bold", size = base_size + 2,
                                         color = "#222222", margin = ggplot2::margin(b = 6)),
      plot.subtitle = ggplot2::element_text(size = base_size - 1, color = "#555555",
                                            margin = ggplot2::margin(b = 8)),
      plot.caption = ggplot2::element_text(size = base_size - 3, color = "#777777",
                                           hjust = 0, margin = ggplot2::margin(t = 8)),
      axis.title = ggplot2::element_text(size = base_size - 1, color = "#333333"),
      axis.text = ggplot2::element_text(size = base_size - 2, color = "#444444"),
      axis.text.x = ggplot2::element_text(angle = 0, hjust = 0.5),
      legend.title = ggplot2::element_text(size = base_size - 1),
      legend.text = ggplot2::element_text(size = base_size - 2),
      legend.position = "bottom",
      legend.key.height = grid::unit(0.6, "lines"),
      strip.text = ggplot2::element_text(face = "bold", size = base_size - 1),
      panel.grid.minor = ggplot2::element_blank(),
      panel.grid.major = ggplot2::element_line(color = "#E8E8E8", linewidth = 0.3),
      panel.border = ggplot2::element_rect(color = "#CCCCCC", fill = NA, linewidth = 0.4),
      plot.margin = ggplot2::margin(10, 12, 8, 10)
    )
}

# Lop hien thi duong 0 va duong trung binh (dung chung).
theme_pgnn_refline <- function(p, color = "#888888", linetype = "dashed") {
  p + ggplot2::geom_hline(yintercept = 0, color = color, linetype = linetype, linewidth = 0.4)
}
