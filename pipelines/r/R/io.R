# R/io.R -- Doc/ghi Parquet va CSV, ghi ket qua vao results/.
#
# Luat phu thuoc (ADR 0001): khong goi Python, khong import tu `packages/`.
# Chi dung `arrow` (fallback: `nanoparquet`), `readr`/`data.table`, `jsonlite`.

# ---------------------------------------------------------------------------
# Duong dan
# ---------------------------------------------------------------------------
repo_root <- function() {
  root <- Sys.getenv("PGNN_REPO_ROOT", unset = "")
  if (nzchar(root) && dir.exists(root)) return(normalizePath(root, winslash = "/"))
  # Suy ra: file nay nam o <repo>/pipelines/r/R/io.R -> len 3 cap len goc repo
  here <- dirname(normalizePath(sys.frame(1)$ofile %||% "pipelines/r/R/io.R", winslash = "/"))
  normalizePath(file.path(here, "..", "..", ".."), winslash = "/")
}

p_res <- function(...) file.path(repo_root(), "results", ...)
p_data <- function(...) file.path(repo_root(), "data", ...)

# ---------------------------------------------------------------------------
# Bien chia tap -- `config/splits.json` (nguon DUY NHAT, dung chung voi
# `phasegnn.data.SPLITS` va Spark G5 `features/splits.py`; khong viet cung).
# ---------------------------------------------------------------------------
# Thu tu do: bien `PGNN_SPLITS_FILE` -> `VNGIS_CONFIG_DIR/splits.json` ->
# `<repo>/config/splits.json`. Tra ve list(splits = list(train = c(from, to), ...),
# harmonic_fit_splits = c("train", ...), test_from = "YYYY-MM-DD", path = ...).
load_splits <- function(path = NULL) {
  if (is.null(path)) {
    path <- Sys.getenv("PGNN_SPLITS_FILE", unset = "")
    if (!nzchar(path)) {
      cd <- Sys.getenv("VNGIS_CONFIG_DIR", unset = "")
      path <- if (nzchar(cd) && file.exists(file.path(cd, "splits.json")))
        file.path(cd, "splits.json") else file.path(repo_root(), "config", "splits.json")
    }
  }
  if (!file.exists(path)) stop("Khong tim thay cau hinh chia tap: ", path, call. = FALSE)
  raw <- jsonlite::fromJSON(path, simplifyVector = TRUE)
  sp <- list()
  for (nm in c("train", "val", "test")) {
    e <- raw$splits[[nm]]
    if (is.null(e)) stop(path, ": thieu tap ", nm, call. = FALSE)
    a <- as.character(e$from); b <- as.character(e$to)
    if (is.na(as.Date(a, "%Y-%m-%d")) || is.na(as.Date(b, "%Y-%m-%d")) || a > b)
      stop(path, ": tap ", nm, " sai dinh dang/khoang: ", a, " -> ", b, call. = FALSE)
    sp[[nm]] <- c(a, b)
  }
  if (!(sp$train[2] < sp$val[1] && sp$val[2] < sp$test[1]))
    stop(path, ": cac tap phai noi tiep, khong chong nhau", call. = FALSE)
  fit <- as.character(raw$harmonic_fit_splits %||% "train")
  if ("test" %in% fit || !all(fit %in% names(sp)))
    stop(path, ": harmonic_fit_splits khong hop le (khong duoc chua 'test'): ",
         paste(fit, collapse = ","), call. = FALSE)
  list(splits = sp, harmonic_fit_splits = fit, test_from = sp$test[1], path = path)
}

# Tao thu muc (khong loi neu da ton tai).
ensure_dir <- function(path) {
  if (!dir.exists(path)) dir.create(path, recursive = TRUE, showWarnings = FALSE)
  invisible(path)
}

# ---------------------------------------------------------------------------
# Doc / ghi Parquet
# ---------------------------------------------------------------------------
# Doc mot dataset Parquet thanh data.frame.
#
# LUU Y QUAN TRONG: `arrow::open_dataset()` KHONG ho tro trong ban `arrow` 26.0.1
# cai dat trong `.rlib` (ban build nay thieu feature "dataset" -> R bi segfault,
# ma loi `0xC0000005`). Vi vay ta DOC TUNG FILE `part-*.parquet` bang
# `arrow::read_parquet()` va noi lai. Duong dan co the la:
#   - mot file `.parquet`          -> doc truc tiep
#   - mot thu muc Spark output     -> noi cac `part-*.parquet` ben trong
#   - mot thu muc phan vung `year=` -> noi cac `year=*/part-*.parquet`
read_parquet_df <- function(path) {
  if (!requireNamespace("arrow", quietly = TRUE)) {
    stop("Thieu goi 'arrow'. Cai: install.packages('arrow', lib='.rlib')", call. = FALSE)
  }
  if (!file.exists(path)) stop("Khong tim thay Parquet: ", path, call. = FALSE)
  if (!dir.exists(path)) {
    return(as.data.frame(arrow::read_parquet(path)))
  }
  files <- list.files(path, pattern = "\\.parquet$", recursive = TRUE, full.names = TRUE)
  files <- files[!grepl("/\\._", gsub("\\\\", "/", files))]
  if (!length(files)) stop("Khong co file part-*.parquet trong: ", path, call. = FALSE)
  parts <- lapply(files, function(f) as.data.frame(arrow::read_parquet(f)))
  # noi cac phan co cung bo cot
  nms <- lapply(parts, names)
  common <- Reduce(intersect, nms)
  parts <- lapply(parts, function(p) p[, common, drop = FALSE])
  do.call(rbind, parts)
}

# Ghi data.frame ra Parquet (mot file).
write_parquet_df <- function(df, path) {
  ensure_dir(dirname(path))
  arrow::write_parquet(df, path)
  invisible(path)
}

# Doc CSV (co header), tu dong doan kieu du lieu.
read_csv_df <- function(path, ...) {
  if (!file.exists(path)) stop("Khong tim thay CSV: ", path, call. = FALSE)
  if (requireNamespace("readr", quietly = TRUE)) {
    return(as.data.frame(readr::read_csv(path, show_col_types = FALSE, progress = FALSE, ...)))
  }
  as.data.frame(utils::read.csv(path, stringsAsFactors = FALSE, ...))
}

# Ghi CSV (UTF-8, dau phay, khong ghi row.names).
write_csv_df <- function(df, path, na = "") {
  ensure_dir(dirname(path))
  utils::write.csv(df, path, row.names = FALSE, fileEncoding = "UTF-8", na = na)
  invisible(path)
}

# Ghi JSON.
write_json_df <- function(obj, path) {
  ensure_dir(dirname(path))
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("Thieu goi 'jsonlite'.", call. = FALSE)
  }
  jsonlite::write_json(obj, path, auto_unbox = TRUE, pretty = TRUE, na = "null")
  invisible(path)
}

# ---------------------------------------------------------------------------
# Luu hinh (PNG) -- dung chung cho toan bao cao
# ---------------------------------------------------------------------------
# `save_fig(expr, path, width, height, dpi)`:
# ve bieu do roi ghi ra PNG. `expr` duoc danh gia trong moi truong moi co bien
# `p` (mot ham ggplot) va `d` (data.frame).
#
# LUU Y QUAN TRONG: `expr` phai duoc `print()` (khong phai chi danh gia) --
# ggplot2 ve lazy, chi khi `print()` moi thuc su render. `force(expr)` chi tao
# doi tuong ggplot chua ve gi -> file PNG rong/rong khong doi.
#
# LUU Y: tren may nay, `png()` voi `units = "in"` that bai khi kich thuoc lon
# ("unable to allocate bitmap" / "cairo error 'invalid value (typically too big)
# for the size of the input'"). Goi `png()` voi `units = "px"` thi moi kich
# thuoc deu chay duoc (da kiem chung 1600x1200 px). Vi vay tham so
# `width`/`height` duoc hieu la **pixel** (khong phai inch).
save_fig <- function(expr, path, width = 900, height = 600, dpi = 96) {
  ensure_dir(dirname(path))
  grDevices::png(filename = path, width = width, height = height, res = dpi,
                 units = "px", type = "cairo", bg = "white")
  on.exit(grDevices::dev.off(), add = TRUE)
  obj <- force(expr)
  print(obj)
  invisible(path)
}
