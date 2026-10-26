"""List the shared libraries that a filly native module imports and reject unexpected ones.

Arguments can be a .pyd, a .so, a wheel that contains one, a MATLAB MEX file (.mexw64,
.mexa64), or an Octave MEX file (.mex). The exit status is 1 if a module imports a library
outside the allowed set for its format and frontend, or if an ELF module exports a symbol other
than its entry points.
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
# MATLAB also ships its own msvcp140.dll, so the same reasoning holds for the MEX file.
SYSTEM_PE = [
    "api-ms-win-crt-*.dll",  # Universal CRT, part of the OS
    "kernel32.dll",
    "user32.dll",  # WGL needs a window class and a device context
    "gdi32.dll",  # pixel formats and SwapBuffers
    "opengl32.dll",  # WGL entry points; drivers are loaded through it
    "shlwapi.dll",  # Filament utils path helpers
]
ALLOWED_PE = SYSTEM_PE + [
    "python3.dll",  # stable ABI
    "python3??.dll",  # version-specific builds for Python 3.10 and 3.11
]
# The C MEX API only: libmat or the C++ MATLAB Data API would tie the file to more of MATLAB.
ALLOWED_MEX_PE = SYSTEM_PE + ["libmx.dll", "libmex.dll"]
# Only the glibc family, the GCC C++ runtime, and the GL/X11 client libraries. manylinux_2_28
# allows all of these, so auditwheel must not graft any library into the wheel.
SYSTEM_ELF = [
    "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1", "ld-linux-x86-64.so.2",
    "libGL.so.1", "libX11.so.6",
]
ALLOWED_ELF = SYSTEM_ELF + ["libstdc++.so.6", "libgcc_s.so.1"]
# MATLAB puts its own, older libstdc++ and libgcc_s first in the library path, so the MATLAB MEX
# file links both statically.
ALLOWED_MEX_ELF = SYSTEM_ELF + ["libmx.so", "libmex.so"]
# Octave runs on the system C++ runtime. Before Octave 10, mkoctfile --mex links no Octave library:
# the MEX API resolves against the running Octave. From Octave 10, it links liboctmex.
ALLOWED_OCTAVE_ELF = ALLOWED_ELF + ["liboctmex.so.*"]
# Exported entry points. Anything else exported could bind to another copy of Filament, libwebp,
# or the C++ runtime in the process. MATLAB's export map adds the API version query.
EXPORTS = {
    "python": ["PyInit__native"],
    "matlab": ["mexFunction", "mexfilerequiredapiversion"],
    # Octave 10 and later check __octave_mex_soversion__ (matlab/CMakeLists.txt).
    "octave": ["mexFunction", "__octave_mex_soversion__"],
}


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


def elf_exports(data):
    """Return the names of the defined global and weak symbols in an ELF image's .dynsym."""
    if data[4] != 2 or data[5] != 1:
        raise ValueError("only little-endian ELF64 is supported")
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shentsize) for index in range(shnum)]
    names = []
    for _, kind, _, _, offset, size, link, _, _, entsize in sections:
        if kind != 11:  # SHT_DYNSYM
            continue
        strtab = sections[link][4]
        for cursor in range(offset + entsize, offset + size, entsize):
            name, info, _, shndx = struct.unpack_from("<IBBH", data, cursor)
            binding = info >> 4
            # Defined, GLOBAL or WEAK. Absolute symbols are version-node names from a version
            # script (MATLAB's export map defines node "MEX"), not code or data.
            if shndx not in (0, 0xFFF1) and binding in (1, 2) and name:
                names.append(data[strtab + name:data.index(b"\0", strtab + name)].decode())
    return names


def imports(data):
    """Return the DLL or DT_NEEDED names of a PE or ELF image."""
    if data[:2] == b"MZ":
        return _pe_imports(data)
    if data[:4] == b"\x7fELF":
        return _elf_needed(data)
    raise ValueError("not a PE or ELF image")


def frontend(label):
    """The frontend of a module from its file name: "matlab", "octave", or "python"."""
    if label.endswith((".mexw64", ".mexa64")):
        return "matlab"
    if label.endswith(".mex"):
        return "octave"
    return "python"


def unexpected(data, kind="python"):
    """Return the imported names that are outside the allowed set for the format and frontend."""
    if data[:2] == b"MZ":
        if kind == "octave":
            raise ValueError("Octave MEX files are built for Linux only")
        allowed = ALLOWED_MEX_PE if kind == "matlab" else ALLOWED_PE
    else:
        allowed = {"python": ALLOWED_ELF, "matlab": ALLOWED_MEX_ELF, "octave": ALLOWED_OCTAVE_ELF}[kind]
    # PE names are case-insensitive; ELF sonames are not.
    fold = str.lower if data[:2] == b"MZ" else str
    return [name for name in imports(data) if not any(fnmatch.fnmatchcase(fold(name), pattern) for pattern in allowed)]


def unexpected_exports(data, kind="python"):
    """Return the exported names of an ELF image that are not its frontend's entry points."""
    if data[:4] != b"\x7fELF":
        return []
    return sorted(name for name in elf_exports(data) if name not in EXPORTS[kind])


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
            kind = frontend(label)
            bad = unexpected(data, kind)
            print(f"{label} ({len(data)} bytes, {kind}): {', '.join(imports(data))}")
            if bad:
                print(f"  unexpected: {', '.join(bad)}")
                failed = True
            extra = unexpected_exports(data, kind)
            if extra:
                shown = ", ".join(extra[:20]) + (f", and {len(extra) - 20} more" if len(extra) > 20 else "")
                print(f"  unexpected exports: {shown}")
                failed = True
    if not found:
        print("no extension module found", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
