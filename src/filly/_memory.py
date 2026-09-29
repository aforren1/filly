"""Windows memory samples for diagnostics, outside the rendering loop."""

import ctypes as c
import os
import sys


MEMORY_COLUMNS = ("working_set_bytes", "private_bytes", "gpu_dedicated_bytes", "gpu_shared_bytes")


class _ProcessMemory(c.Structure):
    _fields_ = [("cb", c.c_uint32), ("faults", c.c_uint32)] + [
        (name, c.c_size_t) for name in ("peak_working", "working", "peak_paged", "paged",
                                      "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]


class _Value(c.Structure):
    # PDH_FMT_LARGE selects the 64-bit integer member of the native union.
    _fields_ = [("status", c.c_uint32), ("value", c.c_int64)]


class _Item(c.Structure):
    _fields_ = [("name", c.c_wchar_p), ("value", _Value)]


class MemorySampler:
    def __init__(self):
        self.query = c.c_void_p()
        self.counters = {}
        self.errors = {}
        self.kernel = self.pdh = None
        if sys.platform != "win32":
            self.errors["platform"] = "Windows memory counters are unavailable"
            return
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetCurrentProcess.argtypes = []
        self.kernel.GetCurrentProcess.restype = c.c_void_p
        self.kernel.K32GetProcessMemoryInfo.argtypes = [c.c_void_p, c.POINTER(_ProcessMemory), c.c_uint32]
        self.kernel.K32GetProcessMemoryInfo.restype = c.c_int
        try:
            self.pdh = c.WinDLL("pdh")
            declarations = {
                "PdhOpenQueryW": [c.c_wchar_p, c.c_size_t, c.POINTER(c.c_void_p)],
                "PdhAddEnglishCounterW": [c.c_void_p, c.c_wchar_p, c.c_size_t, c.POINTER(c.c_void_p)],
                "PdhCollectQueryData": [c.c_void_p],
                "PdhCloseQuery": [c.c_void_p],
                "PdhGetFormattedCounterArrayW": [c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32), c.POINTER(c.c_uint32), c.c_void_p],
            }
            for name, args in declarations.items():
                function = getattr(self.pdh, name)
                function.argtypes, function.restype = args, c.c_uint32
            status = self.pdh.PdhOpenQueryW(None, 0, c.byref(self.query))
            if status:
                raise OSError(f"PdhOpenQueryW: 0x{status:08x}")
            for key, name in (("gpu_dedicated_bytes", "Dedicated Usage"), ("gpu_shared_bytes", "Shared Usage")):
                handle = c.c_void_p()
                status = self.pdh.PdhAddEnglishCounterW(self.query, "\\GPU Process Memory(*)\\" + name, 0, c.byref(handle))
                if status:
                    self.errors[key] = f"PdhAddEnglishCounterW: 0x{status:08x}"
                else:
                    self.counters[key] = handle
        except OSError as exc:
            self.errors["gpu"] = str(exc)
            self.close()

    def _gpu_value(self, key, handle):
        size, count = c.c_uint32(), c.c_uint32()
        status = self.pdh.PdhGetFormattedCounterArrayW(handle, 0x400, c.byref(size), c.byref(count), None)
        if status != 0x800007D2:  # PDH_MORE_DATA
            self.errors[key] = f"PDH size query: 0x{status:08x}"
            return None
        storage = c.create_string_buffer(size.value)
        status = self.pdh.PdhGetFormattedCounterArrayW(handle, 0x400, c.byref(size), c.byref(count), storage)
        if status:
            self.errors[key] = f"PDH values: 0x{status:08x}"
            return None
        items = c.cast(storage, c.POINTER(_Item))
        prefix = f"pid_{os.getpid()}_"
        own = [items[i].value for i in range(count.value) if items[i].name.startswith(prefix)]
        if not own or any(v.status not in (0, 1) or v.value < 0 for v in own):
            self.errors[key] = "No valid counter instance for this process"
            return None
        self.errors.pop(key, None)
        return sum(v.value for v in own)

    def sample(self):
        result = dict.fromkeys(MEMORY_COLUMNS)
        if self.query:
            status = self.pdh.PdhCollectQueryData(self.query)
            if status:
                self.errors["gpu_collect"] = f"PdhCollectQueryData: 0x{status:08x}"
            else:
                self.errors.pop("gpu_collect", None)
                for key, handle in self.counters.items():
                    result[key] = self._gpu_value(key, handle)
        if self.kernel:
            counters = _ProcessMemory()
            counters.cb = c.sizeof(counters)
            if self.kernel.K32GetProcessMemoryInfo(self.kernel.GetCurrentProcess(), c.byref(counters), counters.cb):
                result.update(working_set_bytes=counters.working, private_bytes=counters.private)
                self.errors.pop("process", None)
            else:
                self.errors["process"] = str(c.WinError(c.get_last_error()))
        result["memory_errors"] = dict(self.errors)
        return result

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = c.c_void_p()
        self.counters.clear()
