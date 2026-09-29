"""Lifecycle shared by the host adapters: sizes, acquisition, and close order."""

import numbers
import operator
import warnings

from .. import ImportedTarget, InteropError, Renderer, current_gl_context


def _pixel_size(name, value):
    # Same rules and messages as the native pixel_size(), so both paths report the same error.
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, got bool")
    try:
        return operator.index(value)
    except TypeError:
        pass
    if isinstance(value, numbers.Number) and hasattr(value, "is_integer"):
        if not value.is_integer():
            raise ValueError(f"{name} must be a whole number, got {value!r}")
        return int(value)
    raise TypeError(f"{name} must be an integer, got {type(value).__name__}")


def pixel_sizes(width, height):
    """Coerce a target size as ``Renderer.import_gl_texture`` does."""
    width, height = _pixel_size("width", width), _pixel_size("height", height)
    # The host texture exists before the import validates it, so check the range here too.
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError(f"width and height must be from 1 through 8192, got {width}x{height}")
    return width, height


def shared_renderer(host):
    """Create a renderer that shares the current OpenGL context."""
    context = current_gl_context()
    if not context:
        raise InteropError(f"{host} did not make its OpenGL context current")
    return Renderer(shared_context=context)


class HostTarget(ImportedTarget):
    """A host-owned RGBA8 texture that Filament renders into.

    Subclasses create and release the host objects and draw the texture.
    Preferred close order: this target, then the renderer, then the window. Closing after
    the window also works, but then the host's last GPU work is not fenced.
    """

    # __del__ can run on an instance whose __init__ failed before setup.
    _closed = True
    _context = 0

    def __init__(self, renderer, width, height):
        width, height = pixel_sizes(width, height)
        self._make_host_current()
        self._context = current_gl_context()
        try:
            texture = self._create_host_texture(width, height)
            super().__init__(renderer.import_gl_texture(texture, width=width, height=height))
        except BaseException:
            self._release_host_resources(True)
            raise
        self._closed = False

    # The defaults suit moderngl and zengl, which attach to the current context and cannot
    # make it current. Adapters that own a window override both.
    def _host_open(self):
        """Return whether the host context exists and can be made current."""
        context = current_gl_context()
        return bool(context) and self._context in (0, context)

    def _make_host_current(self):
        """Make the host context current, or raise ``InteropError``."""
        if not self._host_open():
            raise InteropError("Make the window's OpenGL context current first")

    def _create_host_texture(self, width, height):
        """Create the host RGBA8 texture and any draw objects. Return the GL texture name."""
        raise NotImplementedError

    def _release_host_resources(self, host_open):
        """Release the host objects. If ``host_open`` is false, only drop them: a GL delete would fail."""
        raise NotImplementedError

    def _draw(self, x, y, width, height):
        raise NotImplementedError

    def acquire(self):
        """Return a context manager that holds the texture for host sampling.

        The core target counts nesting, so an inner ``acquire()`` does nothing.
        """
        if self._closed:
            raise InteropError("Shared target is closed")
        # An outer acquisition already made the host context current.
        if not self.acquired:
            self._make_host_current()
        return super().acquire()

    def draw(self, x=0, y=0, width=None, height=None):
        """Draw the texture at window pixel (x, y), counted from the lower-left corner.

        Outside ``acquire()``, the draw acquires and releases the texture itself.
        The size defaults to the texture size.
        """
        with self.acquire():
            self._draw(x, y, self.width if width is None else width, self.height if height is None else height)

    def close(self):
        """Release the Filament target and the host objects.

        With the window open, Filament fences the host's GPU work first. If the window is
        already closed, the method makes no host OpenGL calls: Filament waits for its own
        work only, and the host objects go away with the window's context.
        The target is marked closed even if this method raises.
        """
        self._close(switch=True)

    def _close(self, switch):
        if self._closed:
            return
        self._closed = True
        host_open = self._host_open()
        if not switch:
            # Only the target's own context may delete its names: in another context the same
            # names can belong to newer objects.
            host_open = host_open and current_gl_context() == self._context
        try:
            if host_open and switch:
                self._make_host_current()
            super().close()
        finally:
            self._release_host_resources(host_open)

    def __enter__(self):
        if self._closed:
            raise InteropError("Shared target is closed")
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        # Garbage collection runs at arbitrary points, often while another window's context is
        # current. It must not switch contexts, so host objects are released only if this
        # target's context is already current; otherwise they go away with that context.
        if self._closed:
            return
        try:
            warnings.warn(f"{type(self).__name__} was not closed; call close() before closing its window",
                          ResourceWarning, source=self)
            self._close(switch=False)
        except Exception:
            # The host context or Python modules may already be gone during shutdown.
            pass
