"""
Image preprocessor for OCR quality improvement.

Pipeline (each step is independently skippable via config):
  1. Grayscale conversion
  2. Deskew (detect + correct page tilt using projection profile)
  3. Denoise (Gaussian blur with small kernel)
  4. Contrast enhancement (CLAHE via Pillow ImageEnhance)
  5. Binarisation (Otsu's adaptive threshold)
  6. DPI normalisation (upsample to >= 300 DPI)

All steps run synchronously (called from thread pool by OCR engine).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    import PIL
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment, misc]
    ImageEnhance = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]


@dataclass
class PreprocessorConfig:
    """
    Flags to enable/disable each preprocessing step.
    All True by default; set to False to skip for specific document types.
    """
    grayscale: bool = True
    deskew: bool = True
    denoise: bool = True
    contrast_enhance: bool = True
    binarise: bool = True
    dpi_normalise: bool = True
    target_dpi: int = 300


class ImagePreprocessor:
    """
    Prepares document images for maximum OCR accuracy.

    Usage:
        preprocessor = ImagePreprocessor(config)
        cleaned_image = preprocessor.process(pil_image)
    """

    def __init__(self, config: PreprocessorConfig | None = None) -> None:
        self.config = config or PreprocessorConfig()

    def process(self, image: Any) -> Any:
        """
        Run the full preprocessing pipeline on a PIL Image.
        Returns a preprocessed PIL Image suitable for Tesseract.
        """
        if Image is None:
            raise ImportError("Pillow not installed")

        img = image.copy()

        if self.config.grayscale:
            img = self._to_grayscale(img)

        if self.config.deskew and np is not None:
            img = self._deskew(img)

        if self.config.denoise:
            img = self._denoise(img)

        if self.config.contrast_enhance:
            img = self._enhance_contrast(img)

        if self.config.binarise:
            img = self._binarise(img)

        if self.config.dpi_normalise:
            img = self._normalise_dpi(img)

        return img

    def process_bytes(self, image_bytes: bytes) -> bytes:
        """
        Convenience method: accepts raw image bytes, returns processed PNG bytes.
        """
        if Image is None:
            raise ImportError("Pillow not installed")
        import io
        img = Image.open(io.BytesIO(image_bytes))
        processed = self.process(img)
        buf = io.BytesIO()
        processed.save(buf, format="PNG")
        return buf.getvalue()

    # ─── step implementations ──────────────────────────────────────────────────

    def _to_grayscale(self, img: Any) -> Any:
        """Step 1: Convert to grayscale L mode."""
        if img.mode == "L":
            return img
        return img.convert("L")

    def _deskew(self, img: Any) -> Any:
        """
        Step 2: Detect and correct page skew using horizontal projection profile.

        Method:
          - Convert to numpy array
          - For each candidate angle in [-15, +15] degrees (0.5 step)
          - Calculate the variance of the horizontal projection profile
          - The angle with maximum variance is the corrected angle
            (text lines give high variance when horizontal)
        """
        if np is None:
            return img
        try:
            img_array = np.array(img)
            # Binarise for skew detection (not the final binarisation)
            threshold = 128
            binary = (img_array < threshold).astype(np.uint8)

            best_angle = 0.0
            best_variance = -1.0

            for angle in [a * 0.5 for a in range(-30, 31)]:
                rotated = self._rotate_array(binary, angle)
                proj = rotated.sum(axis=1).astype(np.float64)
                variance = float(np.var(proj))
                if variance > best_variance:
                    best_variance = variance
                    best_angle = angle

            if abs(best_angle) > 0.3:  # only rotate if skew > 0.3 degrees
                img = img.rotate(best_angle, expand=True, fillcolor=255)
                logger.debug("Deskew applied", extra={"angle": best_angle})
        except Exception as exc:
            logger.debug("Deskew failed — skipping", extra={"error": str(exc)})
        return img

    def _rotate_array(self, arr: Any, angle: float) -> Any:
        """Rotate a 2D numpy array by angle degrees around center."""
        from PIL import Image as PILImage
        h, w = arr.shape
        pil_img = PILImage.fromarray((arr * 255).astype("uint8"), mode="L")
        rotated = pil_img.rotate(angle, expand=False, fillcolor=0)
        return np.array(rotated) // 255

    def _denoise(self, img: Any) -> Any:
        """
        Step 3: Apply mild Gaussian blur to remove scanner noise.
        Kernel radius 1 removes salt-and-pepper without blurring text edges.
        """
        try:
            return img.filter(ImageFilter.GaussianBlur(radius=1))
        except Exception as exc:
            logger.debug("Denoise failed", extra={"error": str(exc)})
            return img

    def _enhance_contrast(self, img: Any) -> Any:
        """
        Step 4: CLAHE-style local contrast enhancement.

        PIL doesn't have native CLAHE, so we use:
          - ImageOps.equalize for global histogram equalisation
          - ImageEnhance.Contrast with factor 1.5 for local boost
        This approximates CLAHE well for document scans with uneven lighting.
        """
        try:
            img = ImageOps.equalize(img)
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)
        except Exception as exc:
            logger.debug("Contrast enhancement failed", extra={"error": str(exc)})
        return img

    def _binarise(self, img: Any) -> Any:
        """
        Step 5: Otsu's adaptive thresholding.

        Computes the threshold that maximises inter-class variance between
        foreground (text) and background pixels. Works far better than
        fixed threshold for documents with shadows or uneven lighting.
        """
        if np is None:
            # Fallback: fixed 128 threshold
            try:
                return img.point(lambda p: 0 if p < 128 else 255)
            except Exception:
                return img

        try:
            arr = np.array(img)
            threshold = self._otsu_threshold(arr)
            binarised = np.where(arr < threshold, 0, 255).astype(np.uint8)
            return Image.fromarray(binarised, mode="L")
        except Exception as exc:
            logger.debug("Binarisation failed", extra={"error": str(exc)})
            return img

    def _otsu_threshold(self, arr: Any) -> int:
        """Compute Otsu's threshold for a 2D uint8 numpy array."""
        hist, bin_edges = np.histogram(arr.flatten(), bins=256, range=(0, 256))
        hist = hist.astype(np.float64)
        total = hist.sum()
        if total == 0:
            return 128

        current_max = 0.0
        threshold = 128
        sum_total = np.sum(np.arange(256) * hist)
        sum_bg = 0.0
        weight_bg = 0.0

        for t in range(256):
            weight_bg += hist[t]
            if weight_bg == 0:
                continue
            weight_fg = total - weight_bg
            if weight_fg == 0:
                break
            sum_bg += t * hist[t]
            mean_bg = sum_bg / weight_bg
            mean_fg = (sum_total - sum_bg) / weight_fg
            between_class_variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
            if between_class_variance > current_max:
                current_max = between_class_variance
                threshold = t

        return int(threshold)

    def _normalise_dpi(self, img: Any) -> Any:
        """
        Step 6: Ensure image is at least target_dpi (300) before Tesseract.

        DPI info is embedded in the image metadata. If DPI is missing or too
        low, upsample proportionally.
        """
        try:
            dpi = img.info.get("dpi", (72, 72))
            if isinstance(dpi, (int, float)):
                dpi = (dpi, dpi)
            h_dpi = dpi[0] if dpi[0] > 0 else 72
            target = self.config.target_dpi
            if h_dpi < target:
                scale = target / h_dpi
                new_w = int(img.width * scale)
                new_h = int(img.height * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                logger.debug("DPI upsampled", extra={"from_dpi": h_dpi, "scale": scale})
        except Exception as exc:
            logger.debug("DPI normalisation failed", extra={"error": str(exc)})
        return img
