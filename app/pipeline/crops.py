"""Question crop generation utilities."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def crop_regions_and_stitch(page_image_paths: dict[int, Path], regions: list[dict], output_path: Path) -> None:
    """Crop regions from pages and vertically stitch into one image."""
    crops: list[Image.Image] = []
    for region in regions:
        page_no = int(region["page_number"])
        page_path = page_image_paths[page_no]
        with Image.open(page_path) as page:
            width, height = page.size
            left = int(region["x"] * width)
            top = int(region["y"] * height)
            right = int((region["x"] + region["w"]) * width)
            bottom = int((region["y"] + region["h"]) * height)
            crops.append(page.crop((left, top, right, bottom)).copy())

    if not crops:
        raise ValueError("No regions were provided for cropping.")

    stitched_width = max(c.width for c in crops)
    stitched_height = sum(c.height for c in crops)
    stitched = Image.new("RGB", (stitched_width, stitched_height), color=(255, 255, 255))

    y = 0
    for crop in crops:
        stitched.paste(crop, (0, y))
        y += crop.height
        crop.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    stitched.save(output_path, format="PNG")
    stitched.close()
