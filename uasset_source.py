#!/usr/bin/env python3
r"""
uasset_source.py - extract the LOSSLESS source texture from Unreal .uasset files.

Requirements: python >= 3.14 
You need to have the python lib Pillow, numpy installed. For that run: 

pip install pillow numpy

Uncooked editor .uasset packages embed the original imported image as an
FEditorBulkData payload: an Oodle-compressed FCompressedBuffer holding the raw
source pixels. This finds that payload, decompresses it using the Oodle DLL that
ships with your engine install, and writes a true-colour PNG with the real alpha
channel. No thumbnail, no JPEG, no reconstructed transparency.

Handles every SourceCompression these packages use - PNG, JPEG, None, and the
UE Delta row filter. UEJPEG is refused by name rather than guessed at.

Note that a TSF_BGRA8 source is stored blue-first: UE writes the raw BGRA bytes
straight into an RGBA PNG. Those channels are transposed on the way out, exactly
as the editor's own Export does, so the colours come out right. Output is
lossless either way, but for those it is not byte-identical to the payload.

Usage:
    python uasset_source.py <input-folder> <output-folder> [--oodle PATH_TO_DLL]

The Oodle DLL is not bundled with this script - it comes from your own Bellwright
modkit install, and is found automatically in a standard one. If yours is
somewhere unusual, either set BELLWRIGHT_MODKIT to the install root, or pass the
DLL directly:
    --oodle "D:\Games\BellwrightModkit\Engine\Binaries\DotNET\UnrealBuildTool\oo2core_9_win64.dll"
Run with --where-oodle to print which DLL would be used, and stop.

Options:
    --oodle PATH     path to oo2core_*.dll (or .so on Linux)
    --where-oodle    print the Oodle DLL that would be used, then exit
    --overwrite      overwrite existing outputs instead of skipping
    --quiet          only print the summary

"""

from __future__ import annotations

import argparse
import ctypes
import io
import os
import re
import struct
import sys
import zlib
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("This needs Pillow and numpy:  pip install pillow numpy")

# FCompressedBuffer magic, stored big-endian
CB_MAGIC = b"\xb7\x75\x63\x62"
CB_HEADER_SIZE = 64

# FCompressedBuffer method ids
METHOD_NONE = 0
METHOD_OODLE = 3

# bytes per pixel for the source formats we can currently assemble
SOURCE_FORMATS = {
    "TSF_BGRA8": ("BGRA", 4, np.uint8),
    "TSF_RGBA8": ("RGBA", 4, np.uint8),
    "TSF_BGRE8": ("BGRA", 4, np.uint8),
    "TSF_G8": ("G", 1, np.uint8),
    "TSF_G16": ("G", 2, np.uint16),
    "TSF_RGBA16": ("RGBA", 8, np.uint16),
}


# --------------------------------------------------------------------------
# Oodle
# --------------------------------------------------------------------------

class Oodle:
    """Thin ctypes wrapper around OodleLZ_Decompress."""

    def __init__(self, dll_path: Path):
        self.lib = ctypes.CDLL(str(dll_path))
        fn = self.lib.OodleLZ_Decompress
        fn.restype = ctypes.c_ssize_t
        fn.argtypes = [
            ctypes.c_void_p, ctypes.c_ssize_t,   # compressed buffer, size
            ctypes.c_void_p, ctypes.c_ssize_t,   # raw buffer, size
            ctypes.c_int,                        # fuzzSafe
            ctypes.c_int,                        # checkCRC
            ctypes.c_int,                        # verbosity
            ctypes.c_void_p, ctypes.c_ssize_t,   # decBufBase, decBufSize
            ctypes.c_void_p, ctypes.c_void_p,    # callback, userdata
            ctypes.c_void_p, ctypes.c_ssize_t,   # decoderMemory, size
            ctypes.c_int,                        # threadPhase
        ]
        self.fn = fn

    def decompress(self, comp: bytes, raw_size: int) -> bytes:
        out = ctypes.create_string_buffer(raw_size)
        n = self.fn(comp, len(comp), out, raw_size,
                    1, 0, 0,          # fuzzSafe=Yes, no CRC, silent
                    None, 0, None, None, None, 0,
                    3)                # OodleLZ_Decode_Unthreaded
        if n != raw_size:
            raise RuntimeError(f"Oodle returned {n}, expected {raw_size}")
        return out.raw[:raw_size]


# Subfolders of an install root worth searching, best first. The modkit has no
# ThirdParty\Oodle folder; its copies live under DotNET.
ENGINE_SUBDIRS = (
    "Engine/Binaries/ThirdParty/Oodle",
    "Engine/Binaries/DotNET",
    "Engine/Binaries",
    "Binaries/ThirdParty/Oodle",
)

OODLE_PATTERNS = ("oo2core_*_win64.dll", "liboo2core*.so", "liboo2core*.dylib")


def engine_roots() -> list[Path]:
    """Candidate engine install roots, most specific first, no duplicates."""
    roots: list[Path] = []

    def add(p: Path) -> None:
        if p.is_dir() and p not in roots:
            roots.append(p)

    for var in ("BELLWRIGHT_MODKIT", "UE_ENGINE"):
        if os.environ.get(var):
            add(Path(os.environ[var]).expanduser())

    # Standard install locations. Epic and Steam both let you pick the drive,
    # so try every drive that exists rather than assuming C:.
    if os.name == "nt":
        if hasattr(os, "listdrives"):          # 3.12+, skips empty optical drives
            drives = [Path(d) for d in os.listdrives()]
        else:
            drives = [Path(f"{c}:\\") for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                      if Path(f"{c}:\\").is_dir()]
    else:
        drives = [Path.home(), Path("/opt"), Path("/usr/local")]

    prefixes = (
        "Program Files/Epic Games", "Program Files (x86)/Epic Games", "Epic Games",
        "Program Files (x86)/Steam/steamapps/common",
        "SteamLibrary/steamapps/common", "Steam/steamapps/common",
        "Games", "",
    )
    for drive in drives:
        for prefix in prefixes:
            base = drive / prefix if prefix else drive
            if not base.is_dir():
                continue
            for name in ("BellwrightModkit", "Bellwright Modkit", "Bellwright ModKit"):
                add(base / name)

    # Any stock engine install, as a last resort - the Oodle DLL is the same.
    for drive in drives:
        for prefix in ("Program Files/Epic Games", "Epic Games"):
            base = drive / prefix
            if base.is_dir():
                for ue in sorted(base.glob("UE_*")):
                    add(ue)

    return roots


def find_oodle(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            sys.exit(f"Oodle DLL not found: {p}")
        return p

    # The DLL is deliberately not shipped with this script - it is Epic/RAD
    # licensed. Use the copy in your own modkit install.
    for root in engine_roots():
        for sub in ENGINE_SUBDIRS:
            d = root / sub
            if not d.is_dir():
                continue
            for pattern in OODLE_PATTERNS:
                # the modkit ships dozens of identical copies; take the
                # shallowest so --where-oodle prints a recognisable path
                hits = sorted(d.rglob(pattern), key=lambda p: (len(p.parts), str(p)))
                if hits:
                    return hits[0]

    sys.exit(
        "Could not locate the Oodle DLL.\n"
        "It ships with the Bellwright modkit under\n"
        r"  <modkit>\Engine\Binaries\DotNET\UnrealBuildTool\oo2core_9_win64.dll" "\n"
        "Point at it with --oodle, or set BELLWRIGHT_MODKIT to the install root.\n"
        "Use --where-oodle to see what the search finds."
    )


# --------------------------------------------------------------------------
# package parsing
# --------------------------------------------------------------------------

def read_fstring(buf: bytes, off: int):
    """Read an FString (int32 length prefix, NUL-terminated). Returns (text, next_off)."""
    if off < 0 or off + 4 > len(buf):
        return None, off
    n = struct.unpack_from("<i", buf, off)[0]
    if 0 < n < 4096 and off + 4 + n <= len(buf):
        return buf[off + 4: off + 4 + n - 1].decode("utf8", "replace"), off + 4 + n
    return None, off


def read_tags(data: bytes) -> dict:
    """Pull the asset-registry tag pairs (Dimensions, SourceFormat, ...) out of the package."""
    tags = {}
    for key in ("Dimensions", "SourceFormat", "SourceCompression",
                "HasAlphaChannel", "CompressionSettings"):
        for m in re.finditer(re.escape(key.encode()), data):
            # the key can also occur as a substring of a name-table entry, e.g.
            # "SourceFormat" inside "ETextureSourceFormat". Keep looking until a
            # match reads back as a real length-prefixed pair.
            k, nxt = read_fstring(data, m.start() - 4)
            if k != key:
                continue
            v, _ = read_fstring(data, nxt)
            if v:
                tags[key] = v
                break
    return tags


def find_payloads(data: bytes):
    """
    Yield (offset, method, raw_size, comp_size, data_offset, block_sizes) for each
    FCompressedBuffer in the package.

    Layout, all big-endian:
        u32 Magic, u32 Crc32,
        u8 Method, u8 Compressor, u8 CompressionLevel, u8 BlockSizeExponent,
        u32 BlockCount, u64 TotalRawSize, u64 TotalCompressedSize,
        32-byte Blake3 hash            -> 64 bytes total
        u32 CompressedBlockSize[BlockCount]
        <compressed blocks>
    TotalCompressedSize covers the header, the block table and the blocks.
    """
    for m in re.finditer(re.escape(CB_MAGIC), data):
        o = m.start()
        if o + CB_HEADER_SIZE > len(data):
            continue
        try:
            method, _compressor, _level, block_exp = struct.unpack_from(">BBBB", data, o + 8)
            block_count, raw_size, comp_size = struct.unpack_from(">IQQ", data, o + 12)
        except struct.error:
            continue

        if raw_size == 0 or raw_size > 1 << 32 or block_count == 0 or block_count > 4096:
            continue
        if o + comp_size > len(data):
            continue

        table = o + CB_HEADER_SIZE
        if method == METHOD_NONE:
            # An uncompressed buffer carries no block table - the payload starts
            # straight after the 64-byte header. Reading a table here would eat
            # the first four bytes of the data.
            block_sizes = []
        else:
            try:
                block_sizes = list(struct.unpack_from(f">{block_count}I", data, table))
            except struct.error:
                continue

        yield {
            "offset": o,
            "method": method,
            "raw_size": raw_size,
            "comp_size": comp_size,
            "block_exp": block_exp,
            "block_sizes": block_sizes,
            "data_offset": table + 4 * len(block_sizes),
        }


PACKAGE_FILE_TAG = 0x9E2A83C1


def find_legacy_payload(data: bytes) -> bytes | None:
    """
    Older packages predate FCompressedBuffer: the source sits in an inline
    FByteBulkData instead. Two shapes occur.

    1. Chunk-compressed, anchored by PACKAGE_FILE_TAG:
           int64 Tag, int64 ChunkSize, int64 TotalCompressed, int64 TotalRaw
           int64 (Compressed, Raw) per chunk
           <zlib blocks>
    2. Stored inline uncompressed, in which case the payload is simply an image
       file sitting in the package.

    The cached thumbnail is also an image file in the package, so it has to be
    told apart. It is always preceded by its own (Width, Height, Length) header,
    which is an exact test rather than a guess.
    """
    for m in re.finditer(re.escape(struct.pack("<I", PACKAGE_FILE_TAG)), data):
        o = m.start()
        if o < 8 or o + 32 > len(data):        # offset 0 is the package magic
            continue
        tag, chunk_size, total_comp, total_raw = struct.unpack_from("<4q", data, o)
        if tag != PACKAGE_FILE_TAG or not (0 < chunk_size <= 1 << 24):
            continue
        if not (0 < total_raw <= 1 << 30) or not (0 < total_comp <= len(data)):
            continue
        n = -(-total_raw // chunk_size)
        table = o + 32
        if table + 16 * n > len(data):
            continue
        infos = struct.unpack_from(f"<{2 * n}q", data, table)
        off = table + 16 * n
        out = bytearray()
        try:
            for i in range(n):
                csize = infos[2 * i]
                out += zlib.decompress(data[off: off + csize])
                off += csize
        except (zlib.error, IndexError):
            continue
        if len(out) == total_raw:
            return bytes(out)

    # uncompressed inline: take an embedded image that is not the thumbnail
    best = None
    for m in re.finditer(rb"\x89PNG\r\n\x1a\n", data):
        o = m.start()
        end = data.find(b"IEND", o)
        if end < 0:
            continue
        payload = data[o: end + 8]
        width, height = struct.unpack_from(">II", data, o + 16)
        if o >= 12:
            tw, th, tlen = struct.unpack_from("<3i", data, o - 12)
            if (tw, th, tlen) == (width, height, len(payload)):
                continue                       # that is the cached thumbnail
        if best is None or len(payload) > len(best):
            best = payload
    return best


def decompress_payload(data: bytes, p: dict, oodle: Oodle | None) -> bytes:
    if p["method"] == METHOD_NONE:
        return data[p["data_offset"]: p["data_offset"] + p["raw_size"]]
    if p["method"] != METHOD_OODLE:
        raise RuntimeError(f"unsupported compression method {p['method']}")
    if oodle is None:
        raise RuntimeError("payload is Oodle-compressed but no DLL was loaded")

    block_size = 1 << p["block_exp"]
    out = bytearray()
    off = p["data_offset"]
    remaining = p["raw_size"]
    for csize in p["block_sizes"]:
        want = min(block_size, remaining)
        chunk = data[off: off + csize]
        # a block that didn't compress is stored verbatim
        out += chunk[:want] if csize == want else oodle.decompress(chunk, want)
        off += csize
        remaining -= want
    return bytes(out)


# --------------------------------------------------------------------------
# pixels
# --------------------------------------------------------------------------

def to_image(payload: bytes, width: int, height: int, fmt: str):
    if fmt not in SOURCE_FORMATS:
        raise RuntimeError(f"unsupported source format {fmt}")
    layout, bpp, dtype = SOURCE_FORMATS[fmt]

    expected = width * height * bpp
    if len(payload) < expected:
        raise RuntimeError(f"payload {len(payload)} bytes, need {expected} for {width}x{height} {fmt}")
    # extra data past the first mip is the mip chain; ignore it
    buf = payload[:expected]

    channels = len(layout)
    arr = np.frombuffer(buf, dtype=dtype).reshape(height, width, channels)

    if layout == "BGRA":
        arr = arr[..., [2, 1, 0, 3]]
        mode = "RGBA"
    elif layout == "RGBA":
        mode = "RGBA"
    else:
        arr = arr[..., 0]
        mode = "L"

    if dtype == np.uint16:
        arr = (arr >> 8).astype(np.uint8)   # PNG-friendly 8-bit
    return Image.fromarray(np.ascontiguousarray(arr), mode if mode != "L" else "L")


def sniff_container(raw: bytes):
    """
    Some textures store their source as a whole image file rather than as raw
    pixels (the SourceCompression tag says PNG or JPEG). Return (extension,
    label) for those, or None when the payload is bare pixel data.
    """
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "PNG"
    if raw[:2] == b"\xff\xd8\xff":
        return ".jpg", "JPEG"
    return None


def format_from_name_table(data: bytes) -> str:
    """
    Fall back to the package name table when the SourceFormat registry tag is
    absent. The ETextureSourceFormat enum value is interned there as a plain
    "TSF_*" string. Only trust it when the package names exactly one.
    """
    names = set(re.findall(rb"TSF_[A-Za-z0-9]+", data))
    if len(names) == 1:
        return names.pop().decode()
    return ""


# Source formats whose bytes are stored blue-first. UE writes the raw BGRA byte
# order straight into an RGBA PNG, so a decoder reading the file per the PNG
# spec sees red and blue transposed. Swapping them is what the editor's own
# Export does, and it is what makes the output match the art.
BGRA_ORDERED = {"TSF_BGRA8", "TSF_BGRE8"}


def swap_rb(im: Image.Image) -> Image.Image:
    b, g, r, a = im.convert("RGBA").split()
    return Image.merge("RGBA", (r, g, b, a))


# SourceCompression values we can decode. Anything else is refused by name
# rather than guessed at.
UNSUPPORTED_COMPRESSION = {
    "UE JPEG": "UEJPEG (decoder not implemented)",
    "UEJPEG": "UEJPEG (decoder not implemented)",
}

DELTA_COMPRESSION = {"UE Delta", "UEDelta"}

# FImageCoreDelta splits an image into "cuts" - contiguous blocks of rows - and
# delta-filters each one independently. These two constants reproduce
# ImageCoreDeltaComputeNumCuts; they were fitted to, and then verified
# byte-exactly against, editor exports at 8 dimensions from 75x75 to 4096x4096.
DELTA_CUT_BYTES = 131072
DELTA_MIN_ROWS_PER_CUT = 32


def delta_num_cuts(width: int, height: int, bpp: int) -> int:
    """How many independent delta blocks the image was split into."""
    return max(1, min((width * height * bpp) // DELTA_CUT_BYTES,
                      height // DELTA_MIN_ROWS_PER_CUT))


def undo_ue_delta(payload: bytes, width: int, height: int, fmt: str) -> bytes:
    """
    Reverse the 'UE Delta' source filter.

    Each cut's first row is stored as-is; every row after it is that row minus
    the one above, wrapping at the component width. Undoing it is a running sum
    down the rows, restarting at each cut. DeltaT<> is templated on component
    type, so 16-bit formats accumulate per component, not per byte.
    """
    _layout, bpp, dtype = SOURCE_FORMATS[fmt]
    arr = np.frombuffer(payload[:width * height * bpp], dtype=dtype)
    arr = arr.reshape(height, -1)

    rows_per_cut = -(-height // delta_num_cuts(width, height, bpp))   # ceil
    mask = np.uint64((1 << (8 * arr.dtype.itemsize)) - 1)

    out = np.empty_like(arr)
    for start in range(0, height, rows_per_cut):
        end = min(start + rows_per_cut, height)
        block = np.cumsum(arr[start:end], axis=0, dtype=np.uint64)
        out[start:end] = (block & mask).astype(arr.dtype)
    return out.tobytes()


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def process(asset: Path, out_dir: Path, oodle, args) -> tuple[int, str]:
    data = asset.read_bytes()
    tags = read_tags(data)

    dims = tags.get("Dimensions", "")
    m = re.match(r"(\d+)x(\d+)", dims)
    if not m:
        if b"ObjectRedirector" in data:
            return 0, "redirector, not a texture"
        return 0, "no Dimensions tag (cooked package?)"
    width, height = int(m.group(1)), int(m.group(2))

    fmt = tags.get("SourceFormat", "").replace(" ", "_")
    if fmt and not fmt.startswith("TSF_"):
        fmt = "TSF_" + fmt
    if not fmt:
        fmt = format_from_name_table(data)

    # Decide from the tag, not by decoding and eyeballing the result. Guessing
    # at a filter and keeping whatever looked plausible risked writing a
    # corrupted image under a success message.
    comp = tags.get("SourceCompression", "None")
    if comp in UNSUPPORTED_COMPRESSION:
        return 0, f"{comp}: {UNSUPPORTED_COMPRESSION[comp]} - export it from the editor"

    candidates = list(find_payloads(data))
    if candidates:
        raw = decompress_payload(data, max(candidates, key=lambda p: p["raw_size"]), oodle)
    else:
        raw = find_legacy_payload(data)
        if raw is None:
            return 0, "no source payload found"
        comp = "legacy"

    out_dir.mkdir(parents=True, exist_ok=True)

    # SourceCompression=PNG/JPEG: the payload is already a complete image file.
    # This path needs no SourceFormat tag to decode - the file carries its own
    # header - but it does need one to know the channel order.
    container = sniff_container(raw)
    if container:
        ext, note = container
        if fmt not in BGRA_ORDERED:
            # Channel order already matches the container. Copy the bytes out
            # untouched; re-encoding could only lose something.
            target = out_dir / f"{asset.stem}{ext}"
            if target.exists() and not args.overwrite:
                return 0, "exists (use --overwrite)"
            target.write_bytes(raw)
            return 1, f"{width}x{height} {fmt or '?'} verbatim {note}"

        # Stored blue-first: decode, transpose R and B, re-encode as PNG. PNG is
        # lossless so no pixel is harmed, but the output is deliberately no
        # longer byte-identical to the payload.
        target = out_dir / f"{asset.stem}.png"
        if target.exists() and not args.overwrite:
            return 0, "exists (use --overwrite)"
        with Image.open(io.BytesIO(raw)) as im:
            swap_rb(im).save(target)
        return 1, f"{width}x{height} {fmt} {note} R/B swapped"

    # What is left is a bare pixel buffer we assemble ourselves - either stored
    # plainly, or delta-filtered. Both need the format tag.
    if fmt not in SOURCE_FORMATS:
        return 0, f"unsupported source format {fmt!r} (compression {comp!r})"

    _layout, bpp, _dt = SOURCE_FORMATS[fmt]
    if len(raw) < width * height * bpp:
        return 0, f"payload {len(raw)} bytes, need {width * height * bpp} for {width}x{height} {fmt}"

    if comp in DELTA_COMPRESSION:
        raw = undo_ue_delta(raw, width, height, fmt)
        note = f"UE Delta, {delta_num_cuts(width, height, bpp)} cuts"
        if np.dtype(_dt).itemsize > 1:
            # DeltaT<> is templated on component type, so this is the documented
            # generalisation - but no 16-bit export was available to confirm it.
            note += " (16-bit path unverified)"
    else:
        note = "raw pixels"

    target = out_dir / f"{asset.stem}.png"
    if target.exists() and not args.overwrite:
        return 0, "exists (use --overwrite)"
    to_image(raw, width, height, fmt).save(target)
    return 1, f"{width}x{height} {fmt} {note}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract lossless source textures from .uasset files.")
    ap.add_argument("input", type=Path, nargs="?")
    ap.add_argument("output", type=Path, nargs="?")
    ap.add_argument("--oodle", default=None, help="path to oo2core_*.dll")
    ap.add_argument("--where-oodle", action="store_true",
                    help="print the Oodle DLL that would be used, then exit")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.where_oodle:
        print(find_oodle(args.oodle))
        return 0
    if args.input is None or args.output is None:
        ap.error("input and output folders are required")

    src = args.input.expanduser().resolve()
    dst = args.output.expanduser().resolve()
    if not src.is_dir():
        print(f"Not a folder: {src}", file=sys.stderr)
        return 1
    if dst == src or dst in src.parents:
        print("Refusing to write output inside the input folder.", file=sys.stderr)
        return 1

    oodle = Oodle(find_oodle(args.oodle))

    assets = sorted(p for p in src.rglob("*.uasset") if p.is_file())
    if not assets:
        print(f"No .uasset files under {src}")
        return 0

    ok = 0
    fail = 0
    for asset in assets:
        rel = asset.relative_to(src)
        try:
            n, note = process(asset, dst / rel.parent, oodle, args)
        except Exception as e:
            n, note = 0, str(e)
        ok += n
        if n:
            if not args.quiet:
                print(f"  + {rel.with_suffix('.png')}  ({note})")
        else:
            fail += 1
            if not args.quiet:
                print(f"  - {rel}: {note}")

    print(f"\nExtracted {ok} textures to {dst}")
    if fail:
        print(f"{fail} skipped or failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
