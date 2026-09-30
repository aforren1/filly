"""List the shared libraries that the filly extension module imports and reject unexpected ones.

Arguments can be a .pyd, a .so, or a wheel that contains one. The exit status is 1 if a module
imports a library outside the allowed set for its format.
"""

import argparse
import fnmatch
from pathlib import Path
import struct
import sys
import zipfile

# The C++ runtime and vcruntime are linked statically; the UCRT is an OS component on Windows 10+.
# Python itself does not ship MSVCP140.dll, so importing it makes the module depend on the
# Visual C++ Redistributable and on whichever msvcp140.dll the host process loaded first.
ALLOWED_PE = [
    "api-ms-win-crt-*.dll",  # Universal CRT, part of the OS
    "kernel32.dll",
    "user32.dll",  # WGL needs a window class and a device context
    "gdi32.dll",  # pixel formats and SwapBuffers
    "opengl32.dll",  # WGL entry points; drivers are loaded through it
    "shlwapi.dll",  # Filament utils path helpers
    "python3.dll",  # stable ABI
    "python3??.dll",  # version-specific builds for Python 3.10 and 3.11
]
# Only the glibc family, the GCC C++ runtime, and the GL/X11 client libraries. manylinux_2_28
# allows all of these, so auditwheel must not graft any library into the wheel.
ALLOWED_ELF = [
    "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1", "ld-linux-x86-64.so.2",
    "libstdc++.so.6", "libgcc_s.so.1",
    "libGL.so.1", "libX11.so.6",
]


def _pe_imports(data):
    header = struct.unpack_from("<I", data, 0x3C)[0]
    if data[header:header + 4] != b"PE\0\0":
        raise ValueError("not a PE image")
    sections, optional_size = struct.unpack_from("<2xH12xH", data, header + 4)
    optional = header + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (112 if magic == 0x20B else 96)
    table = optional + optional_size
    spans = [struct.unpack_from("<8xIIII", data, table + 40 * index) for index in range(sections)]

    def offset(rva):
        for size, address, raw_size, raw in spans:
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError(f"RVA {rva:#x} is outside all sections")

    def name(rva):
        start = offset(rva)
        return data[start:data.index(b"\0", start)].decode("ascii")

    names = []
    # Import directory (index 1) and delay-load directory (index 13).
    for index, size, name_field in ((1, 20, 12), (13, 32, 4)):
        rva = struct.unpack_from("<I", data, directories + 8 * index)[0]
        if not rva:
            continue
        cursor = offset(rva)
        while any(data[cursor:cursor + size]):
            names.append(name(struct.unpack_from("<I", data, cursor + name_field)[0]))
            cursor += size
    return names


def _elf_needed(data):
    if data[4] != 2 or data[5] != 1:
        raise ValueError("only little-endian ELF64 is supported")
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dynamic = [], None
    for index in range(phnum):
        kind, _, file_offset, vaddr, _, file_size, _, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phentsize)
        if kind == 1:
            loads.append((vaddr, file_offset, file_size))
        elif kind == 2:
            dynamic = (file_offset, file_size)
    if dynamic is None:
        return []

    def offset(vaddr):
        for start, file_offset, size in loads:
            if start <= vaddr < start + size:
                return file_offset + vaddr - start
        raise ValueError(f"address {vaddr:#x} is outside all segments")

    needed, strtab = [], None
    for cursor in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, value = struct.unpack_from("<qQ", data, cursor)
        if tag == 0:
            break
        if tag == 1:
            needed.append(value)
        elif tag == 5:
            strtab = offset(value)
    return [data[strtab + item:data.index(b"\0", strtab + item)].decode() for item in needed]


def imports(data):
    """Return the DLL or DT_NEEDED names of a PE or ELF image."""
    if data[:2] == b"MZ":
        return _pe_imports(data)
    if data[:4] == b"\x7fELF":
        return _elf_needed(data)
    raise ValueError("not a PE or ELF image")


def unexpected(data):
    """Return the imported names that are outside the allowed set for the image format."""
    allowed = ALLOWED_PE if data[:2] == b"MZ" else ALLOWED_ELF
    # PE names are case-insensitive; ELF sonames are not.
    fold = str.lower if data[:2] == b"MZ" else str
    return [name for name in imports(data) if not any(fnmatch.fnmatchcase(fold(name), pattern) for pattern in allowed)]


def _modules(path):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as wheel:
            for member in wheel.namelist():
                if Path(member).name.startswith("_native") and member.endswith((".pyd", ".so")):
                    yield f"{path.name}:{member}", wheel.read(member)
    else:
        yield str(path), path.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    failed = found = False
    for path in args.paths:
        for label, data in _modules(path):
            found = True
            bad = unexpected(data)
            print(f"{label} ({len(data)} bytes): {', '.join(imports(data))}")
            if bad:
                print(f"  unexpected: {', '.join(bad)}")
                failed = True
    if not found:
        print("no extension module found", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
