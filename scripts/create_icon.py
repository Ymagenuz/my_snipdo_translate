from __future__ import annotations

import argparse
import binascii
import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_SIZES = (16, 32, 48, 64, 128, 256)
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "snipdo_script_logo" / "gemini-color.png"
DESTINATION = ROOT / "snipdo_script_logo" / "SnipDoTranslate.ico"


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _read_png_rgba(path: Path) -> tuple[int, int, bytes]:
    blob = path.read_bytes()
    if not blob.startswith(PNG_SIGNATURE):
        raise ValueError("icon source is not a PNG file")

    offset = len(PNG_SIGNATURE)
    header: tuple[int, int, int, int, int, int, int] | None = None
    palette = b""
    transparency = b""
    compressed_parts: list[bytes] = []
    saw_end = False

    while offset < len(blob):
        if offset + 12 > len(blob):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack_from(">I", blob, offset)[0]
        chunk_type = blob[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(blob):
            raise ValueError("truncated PNG chunk data")
        chunk_data = blob[data_start:data_end]
        expected_crc = struct.unpack_from(">I", blob, data_end)[0]
        actual_crc = binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError("PNG chunk CRC mismatch")

        if chunk_type == b"IHDR":
            if header is not None or len(chunk_data) != 13:
                raise ValueError("invalid PNG header")
            header = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"PLTE":
            palette = chunk_data
        elif chunk_type == b"tRNS":
            transparency = chunk_data
        elif chunk_type == b"IDAT":
            compressed_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            saw_end = True
            offset = crc_end
            break
        offset = crc_end

    if not saw_end or offset != len(blob) or header is None:
        raise ValueError("incomplete PNG file")

    width, height, bit_depth, color_type, compression, filtering, interlace = header
    if width <= 0 or height <= 0:
        raise ValueError("invalid PNG dimensions")
    if bit_depth != 8 or color_type not in {2, 3, 6}:
        raise ValueError("icon source must use 8-bit RGB, indexed, or RGBA pixels")
    if compression != 0 or filtering != 0 or interlace != 0:
        raise ValueError("icon source uses unsupported PNG encoding")

    channels = {2: 3, 3: 1, 6: 4}[color_type]
    stride = width * channels
    raw = zlib.decompress(b"".join(compressed_parts))
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG pixel stream has an unexpected length")

    rows: list[bytes] = []
    previous = bytearray(stride)
    position = 0
    for _row_number in range(height):
        filter_type = raw[position]
        position += 1
        row = bytearray(raw[position : position + stride])
        position += stride
        if filter_type > 4:
            raise ValueError("PNG row uses an unknown filter")

        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                predictor = 0
            row[index] = (row[index] + predictor) & 0xFF
        rows.append(bytes(row))
        previous = row

    rgba = bytearray(width * height * 4)
    output = 0
    if color_type == 6:
        for row in rows:
            rgba[output : output + len(row)] = row
            output += len(row)
    elif color_type == 2:
        for row in rows:
            for index in range(0, len(row), 3):
                rgba[output : output + 4] = row[index : index + 3] + b"\xFF"
                output += 4
    else:
        if not palette or len(palette) % 3:
            raise ValueError("indexed PNG is missing a valid palette")
        palette_entries = len(palette) // 3
        for row in rows:
            for palette_index in row:
                if palette_index >= palette_entries:
                    raise ValueError("indexed PNG references a missing palette entry")
                palette_offset = palette_index * 3
                alpha = (
                    transparency[palette_index]
                    if palette_index < len(transparency)
                    else 255
                )
                rgba[output : output + 4] = (
                    palette[palette_offset : palette_offset + 3] + bytes((alpha,))
                )
                output += 4
    return width, height, bytes(rgba)


def _axis_sample(source_size: int, destination_size: int, coordinate: int) -> tuple[int, int, int, int]:
    denominator = 2 * destination_size
    numerator = (2 * coordinate + 1) * source_size - destination_size
    lower = numerator // denominator
    fraction = numerator - lower * denominator
    if lower < 0:
        return 0, 0, denominator, 0
    if lower >= source_size - 1:
        edge = source_size - 1
        return edge, edge, denominator, 0
    return lower, lower + 1, denominator - fraction, fraction


def _resize_rgba(source: bytes, width: int, height: int, size: int) -> bytes:
    destination = bytearray(size * size * 4)
    x_samples = [_axis_sample(width, size, x) for x in range(size)]
    y_samples = [_axis_sample(height, size, y) for y in range(size)]
    output = 0

    for y0, y1, y_weight0, y_weight1 in y_samples:
        for x0, x1, x_weight0, x_weight1 in x_samples:
            weighted_pixels = (
                ((y0 * width + x0) * 4, y_weight0 * x_weight0),
                ((y0 * width + x1) * 4, y_weight0 * x_weight1),
                ((y1 * width + x0) * 4, y_weight1 * x_weight0),
                ((y1 * width + x1) * 4, y_weight1 * x_weight1),
            )
            total_weight = sum(weight for _offset, weight in weighted_pixels)
            alpha_sum = sum(source[pixel + 3] * weight for pixel, weight in weighted_pixels)
            alpha = (alpha_sum + total_weight // 2) // total_weight

            if alpha_sum:
                for channel in range(3):
                    premultiplied = sum(
                        source[pixel + channel] * source[pixel + 3] * weight
                        for pixel, weight in weighted_pixels
                    )
                    destination[output + channel] = (
                        premultiplied + alpha_sum // 2
                    ) // alpha_sum
            destination[output + 3] = alpha
            output += 4
    return bytes(destination)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _encode_rgba_png(size: int, pixels: bytes) -> bytes:
    stride = size * 4
    rows = b"".join(
        b"\x00" + pixels[offset : offset + stride]
        for offset in range(0, len(pixels), stride)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows, level=9))
        + _png_chunk(b"IEND", b"")
    )


def build_icon(source_path: Path = SOURCE) -> bytes:
    width, height, source = _read_png_rgba(source_path)
    images = [
        _encode_rgba_png(size, _resize_rgba(source, width, height, size))
        for size in ICON_SIZES
    ]

    header = struct.pack("<HHH", 0, 1, len(images))
    image_offset = len(header) + 16 * len(images)
    entries: list[bytes] = []
    for size, image in zip(ICON_SIZES, images):
        dimension = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(image),
                image_offset,
            )
        )
        image_offset += len(image)
    return header + b"".join(entries) + b"".join(images)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the deterministic Windows icon")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the tracked icon does not match the source PNG",
    )
    args = parser.parse_args()
    expected = build_icon()

    if args.check:
        if not DESTINATION.is_file() or DESTINATION.read_bytes() != expected:
            raise SystemExit("SnipDoTranslate.ico is missing or out of date")
        print("SnipDoTranslate.ico matches gemini-color.png")
        return 0

    temporary = DESTINATION.with_suffix(".ico.tmp")
    temporary.write_bytes(expected)
    temporary.replace(DESTINATION)
    print("generated SnipDoTranslate.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
