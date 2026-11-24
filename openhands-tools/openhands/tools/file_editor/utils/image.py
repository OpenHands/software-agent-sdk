import io
from typing import BinaryIO

from PIL import Image, features


MAX_PREVIEW_DIMENSION = 1024
MAX_PREVIEW_BYTES = 128 * 1024


def image_view(
    source: BinaryIO, path: str, region: list[int] | None = None
) -> tuple[bytes, str, str]:
    with Image.open(source, formats=("PNG", "JPEG", "GIF", "WEBP", "BMP")) as image:
        width, height = image.size
        box = None
        if region is not None:
            if len(region) != 4:
                raise ValueError("image_region needs four pixel coordinates")
            left, top, right, bottom = region
            box = (left, top, right, bottom)
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError(
                    f"image_region must be inside the {width}x{height} image"
                )
        elif max(image.size) <= MAX_PREVIEW_DIMENSION:
            source.seek(0)
            original = source.read(MAX_PREVIEW_BYTES + 1)
            if len(original) <= MAX_PREVIEW_BYTES:
                return (
                    original,
                    Image.MIME[image.format or "PNG"],
                    f"Image file {path} read successfully.",
                )
            source.seek(0)

        frame = " First frame only." if getattr(image, "is_animated", False) else ""
        preview = image.crop(box) if box is not None else image
        preview.thumbnail(
            (MAX_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION), Image.Resampling.LANCZOS
        )
        preview = preview.convert("RGBA" if preview.has_transparency_data else "RGB")
        preview.info.clear()
        output = io.BytesIO()
        image_format = "PNG"
        preview.save(output, format=image_format)
        if output.tell() > MAX_PREVIEW_BYTES:
            image_format = "WEBP" if features.check("webp") else "PNG"
            while True:
                output = io.BytesIO()
                preview.save(output, format=image_format, quality=85)
                if output.tell() <= MAX_PREVIEW_BYTES:
                    break
                preview.thumbnail(
                    (max(1, preview.width * 3 // 4), max(1, preview.height * 3 // 4)),
                    Image.Resampling.LANCZOS,
                )
        area = f" of region {region}" if region is not None else ""
        message = (
            f"Preview {preview.width}x{preview.height}{area} from {path} "
            f"({width}x{height}).{frame} Use image_region on the original for detail."
        )
        return output.getvalue(), Image.MIME[image_format], message
