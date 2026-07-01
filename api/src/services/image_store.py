from __future__ import annotations

import hashlib
import shutil
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageEnhance, ImageOps, ImageStat, UnidentifiedImageError

from ..config import settings

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}
ALLOWED_FORMATS = {"JPEG", "PNG"}


def card_dir(card_id: str) -> Path:
    return settings.data_dir / "cards" / card_id


def relative_path(path: Path) -> str:
    return str(path.relative_to(settings.data_dir))


def resolve_data_path(relative: str) -> Path:
    path = (settings.data_dir / relative).resolve()
    data_root = settings.data_dir.resolve()
    if data_root not in path.parents and path != data_root:
        raise HTTPException(status_code=400, detail="Invalid path")
    return path


def make_card_id() -> str:
    return uuid4().hex


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def save_original_upload(file: UploadFile, card_id: str, side: str = "front") -> Path:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG and PNG images are supported",
        )

    directory = card_dir(card_id)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = ".png" if file.content_type == "image/png" else ".jpg"
    stem = "original" if side == "front" else f"original_{side}"
    original_path = directory / f"{stem}{suffix}"

    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with original_path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                _cleanup_failed_upload(directory, original_path, side)
                raise HTTPException(status_code=413, detail="Uploaded image is too large")
            out.write(chunk)

    _validate_original_image(directory, original_path, side)

    return original_path


def save_original_bytes(
    data: bytes,
    content_type: str,
    card_id: str,
    side: str = "front",
) -> Path:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail="Uploaded image is too large")

    content_type = content_type.split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        content_type = _detect_content_type(data)

    directory = card_dir(card_id)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = ".png" if content_type == "image/png" else ".jpg"
    stem = "original" if side == "front" else f"original_{side}"
    original_path = directory / f"{stem}{suffix}"
    original_path.write_bytes(data)

    _validate_original_image(directory, original_path, side)

    return original_path


def _detect_content_type(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format == "JPEG":
                return "image/jpeg"
            if image.format == "PNG":
                return "image/png"
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=415, detail="Invalid image file") from exc
    raise HTTPException(status_code=415, detail="Only JPEG and PNG images are supported")


def _cleanup_failed_upload(directory: Path, original_path: Path, side: str) -> None:
    if side == "front":
        shutil.rmtree(directory, ignore_errors=True)
    else:
        original_path.unlink(missing_ok=True)


def _validate_original_image(directory: Path, original_path: Path, side: str) -> None:
    try:
        with Image.open(original_path) as img:
            if img.format not in ALLOWED_FORMATS:
                raise HTTPException(status_code=415, detail="Only JPEG and PNG images are supported")
            img.verify()
    except HTTPException:
        _cleanup_failed_upload(directory, original_path, side)
        raise
    except UnidentifiedImageError as exc:
        _cleanup_failed_upload(directory, original_path, side)
        raise HTTPException(status_code=415, detail="Invalid image file") from exc


def create_processed_images(original_path: Path, card_id: str, side: str = "front") -> tuple[Path, Path]:
    directory = card_dir(card_id)
    processed_name = "processed.jpg" if side == "front" else f"processed_{side}.jpg"
    thumbnail_name = "thumbnail.jpg" if side == "front" else f"thumbnail_{side}.jpg"
    processed_path = directory / processed_name
    thumbnail_path = directory / thumbnail_name

    with Image.open(original_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        processed = _autocrop_and_correct(image)
        processed = _enhance_processed_image(processed)
        processed.save(processed_path, "JPEG", quality=92, optimize=True)

        thumb = processed.copy()
        thumb.thumbnail((720, 720))
        thumb.save(thumbnail_path, "JPEG", quality=84, optimize=True)

    return processed_path, thumbnail_path


def rotate_processed_images(processed_path: Path, thumbnail_path: Path, degrees: int) -> None:
    with Image.open(processed_path) as source:
        rotated = source.convert("RGB").rotate(degrees, expand=True)
        rotated.save(processed_path, "JPEG", quality=92, optimize=True)
        _save_thumbnail(rotated, thumbnail_path)


def rotate_page_image(image_path: Path, thumbnail_path: Path, degrees: int) -> None:
    if degrees not in {-90, 90}:
        raise HTTPException(status_code=400, detail="Invalid rotation")

    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        rotated = image.rotate(-degrees, expand=True)
        _save_page_image(rotated, image_path)
        _save_thumbnail(rotated, thumbnail_path)


def _save_page_image(image: Image.Image, path: Path) -> None:
    if path.suffix.lower() == ".png":
        image.save(path, "PNG", optimize=True)
        return
    image.save(path, "JPEG", quality=92, optimize=True)


def image_metadata(path: Path) -> tuple[int | None, int | None, int | None]:
    if not path.exists():
        return None, None, None
    file_size = path.stat().st_size
    try:
        with Image.open(path) as image:
            width, height = image.size
        return width, height, file_size
    except Exception:
        return None, None, file_size


def save_rotated_candidate(source_path: Path, degrees: int) -> Path:
    candidate_path = source_path.with_name(f"{source_path.stem}_rotate_{degrees}.jpg")
    with Image.open(source_path) as source:
        source.convert("RGB").rotate(degrees, expand=True).save(
            candidate_path,
            "JPEG",
            quality=92,
            optimize=True,
        )
    return candidate_path


def _save_thumbnail(image: Image.Image, thumbnail_path: Path) -> None:
    thumb = image.copy()
    thumb.thumbnail((720, 720))
    thumb.save(thumbnail_path, "JPEG", quality=84, optimize=True)


def _enhance_processed_image(image: Image.Image) -> Image.Image:
    image = ImageOps.autocontrast(image.convert("RGB"), cutoff=1)
    luminance = ImageStat.Stat(image.convert("L")).mean[0]

    if luminance < 155:
        factor = min(1.65, max(1.0, 172 / max(luminance, 1)))
        image = ImageEnhance.Brightness(image).enhance(factor)
    elif luminance > 225:
        factor = max(0.86, 210 / luminance)
        image = ImageEnhance.Brightness(image).enhance(factor)

    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Sharpness(image).enhance(1.08)
    return image


def _autocrop_and_correct(image: Image.Image) -> Image.Image:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return image

    rgb = np.array(image)
    height, width = rgb.shape[:2]
    if width < 200 or height < 200:
        return image

    scale = min(1.0, 1400 / max(width, height))
    resized = cv2.resize(rgb, (int(width * scale), int(height * scale))) if scale < 1 else rgb
    gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(gray, 30, 90)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edges = cv2.dilate(edges, kernel, iterations=1)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=3)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image

    resized_area = resized.shape[0] * resized.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = cv2.contourArea(contour)
        if area < resized_area * 0.08:
            continue

        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4:
            continue

        points = approx.reshape(4, 2).astype("float32") / scale
        corrected = _perspective_warp(rgb, points)
        if corrected is not None:
            return Image.fromarray(corrected)

    corrected = _bright_region_warp(rgb, resized, scale)
    if corrected is not None:
        return Image.fromarray(corrected)

    return image


def _bright_region_warp(rgb, resized, scale: float):
    import cv2
    import numpy as np

    gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (9, 9), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    resized_height, resized_width = resized.shape[:2]
    resized_area = resized.shape[0] * resized.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(contour)
        if area < resized_area * 0.12 or area > resized_area * 0.95:
            continue

        x, y, width, height = cv2.boundingRect(contour)
        margin = 4
        if (
            x <= margin
            or y <= margin
            or x + width >= resized_width - margin
            or y + height >= resized_height - margin
        ):
            continue

        long_side = max(width, height)
        short_side = min(width, height)
        ratio = long_side / short_side if short_side else 0
        if ratio < 1.15 or ratio > 2.25:
            continue

        rect = cv2.minAreaRect(contour)
        box = cv2.boxPoints(rect).astype("float32") / scale
        corrected = _perspective_warp(rgb, box)
        if corrected is not None:
            return corrected

    return None


def _perspective_warp(rgb, points):
    import cv2
    import numpy as np

    rect = _order_points(points)
    rect = _expand_points(rect, rgb.shape)
    tl, tr, br, bl = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_width = int(max(width_a, width_b))
    max_height = int(max(height_a, height_b))

    if max_width < 180 or max_height < 100:
        return None

    long_side = max(max_width, max_height)
    short_side = min(max_width, max_height)
    ratio = long_side / short_side if short_side else 0
    if ratio < 1.15 or ratio > 2.25:
        return None

    destination = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, destination)
    return cv2.warpPerspective(rgb, matrix, (max_width, max_height))


def _order_points(points):
    import numpy as np

    rect = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1)
    rect[0] = points[np.argmin(sums)]
    rect[2] = points[np.argmax(sums)]
    rect[1] = points[np.argmin(diffs)]
    rect[3] = points[np.argmax(diffs)]
    return rect


def _expand_points(rect, image_shape, amount: float = 0.015):
    import numpy as np

    height, width = image_shape[:2]
    center = rect.mean(axis=0)
    expanded = center + (rect - center) * (1 + amount)
    expanded[:, 0] = np.clip(expanded[:, 0], 0, width - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, height - 1)
    return expanded.astype("float32")
