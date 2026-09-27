"""Platform-dependent operations, isolated from the application workflow."""
import errno
import os
from pathlib import Path
import re
import sys


def sync_directory(path):
    """Persist directory metadata on supported POSIX filesystems.

    Windows cannot open/fsync a directory through Python's os API. File
    flush/fsync and os.replace are still performed by callers on both platforms.
    Unsupported directory syncing is optional; genuine I/O errors still raise.
    """
    directory_flag = getattr(os, 'O_DIRECTORY', None)
    if os.name != 'posix' or directory_flag is None:
        return False
    unsupported = {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}
    try:
        descriptor = os.open(path, os.O_RDONLY | directory_flag)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        if error.errno not in unsupported:
            raise
        return False
    return True


def portable_filename(filename, max_bytes=180):
    """Keep a readable UTF-8 upload name valid on Linux and Windows."""
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename).rstrip(' .')
    device = filename.split('.', 1)[0].rstrip(' ').upper()
    if device in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', device):
        filename = '_' + filename
    suffix = Path(filename).suffix
    stem = filename[:-len(suffix)] if suffix else filename
    budget = max_bytes - len(suffix.encode('utf-8'))
    stem = stem.encode('utf-8')[:budget].decode('utf-8', errors='ignore').rstrip(' .')
    return (stem or 'upload') + suffix


def process_peak_rss_mb():
    """Process-lifetime peak resident RAM in MiB, or None if unavailable."""
    if os.name == 'nt':
        # Load Windows-only APIs inside this branch so Linux can import normally.
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            psapi = ctypes.WinDLL('psapi', use_last_error=True)
            current_process = kernel32.GetCurrentProcess
            current_process.argtypes = []
            current_process.restype = wintypes.HANDLE
            memory_info = psapi.GetProcessMemoryInfo
            memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
            memory_info.restype = wintypes.BOOL
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not memory_info(current_process(), ctypes.byref(counters), counters.cb):
                return None
            peak_bytes = counters.PeakWorkingSetSize
        except (AttributeError, OSError):
            return None
    else:
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        except (ImportError, AttributeError, OSError):
            return None
        peak_bytes = peak if sys.platform == 'darwin' else peak * 1024
    return round(peak_bytes / (1024 * 1024), 2)
