"""Lifecycle shared by the host adapters: sizes, acquisition, drawing, and close order."""

import numbers
import operator
import warnings
import weakref

from .. import ImportedTarget, InteropError, Renderer, _native, current_gl_context

# macOS builds only: a target for host contexts that cannot share objects with Filament's.
_CopyTarget = getattr(_native, "_CopyTarget", None)


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
    """Coerce a target size as ``Renderer.import_gl_target`` does."""
    width, height = _pixel_size("width", width), _pixel_size("height", height)
    # The host texture exists before the import validates it, so check the range here too.
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError(f"width and height must be from 1 through 8192, got {width}x{height}")
    return width, height


def draw_viewport(viewport, default):
    """Coerce ``draw(viewport=...)`` as ``Renderer.render`` coerces its viewport."""
    if viewport is None:
        return default
    values = tuple(viewport)
    if len(values) != 4:
        raise ValueError("viewport must be (x, y, width, height)")
    names = ("viewport x", "viewport y", "viewport width", "viewport height")
    x, y, width, height = (_pixel_size(name, value) for name, value in zip(names, values))
    if width < 1 or height < 1:
        raise ValueError(f"viewport width and height must be positive, got {width}x{height}")
    return x, y, width, height


def shared_renderer(host):
    """Create a renderer that shares the current OpenGL context."""
    context = current_gl_context()
    if not context:
        raise InteropError(f"{host} did not make its OpenGL context current")
    return Renderer(shared_context=context)


def _close_with_renderer(target):
    # The renderer holds this callback; a strong reference to the target would keep it alive
    # through the native target, out of reach of the cycle collector.
    reference = weakref.ref(target)

    def close():
        target = reference()
        if target is not None:
            target.close()

    return close


class _HostTargetLifecycle:
    # The methods of every host target. The native base, which the subclasses below add, is the
    # target type that reaches the host texture.

    # __del__ can run on an instance whose __init__ failed before setup.
    _closed = True
    _context = 0

    def __init__(self, renderer, width, height):
        self._make_host_current()
        self._context = current_gl_context()
        if width is None or height is None:
            size = self._default_size()
            width, height = size[0] if width is None else width, size[1] if height is None else height
        width, height = pixel_sizes(width, height)
        try:
            texture = self._create_host_texture(width, height)
            super().__init__(self._create_native(renderer, texture, width, height))
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

    def _default_size(self):
        """Return the size of a target that covers the host's framebuffer."""
        raise TypeError(f"{type(self).__name__} needs width and height: the host does not report a size")

    def _create_host_texture(self, width, height):
        """Create the host RGBA8 texture and any draw objects. Return the GL texture name."""
        raise NotImplementedError

    def _release_host_resources(self, host_open):
        """Release the host objects. If ``host_open`` is false, only drop them: a GL delete would fail."""
        raise NotImplementedError

    def _draw(self, x, y, width, height, blend):
        raise NotImplementedError

    def acquire(self):
        """Return a context manager that holds the texture for host sampling.

        The core target counts nesting, so an inner ``acquire()`` does nothing.
        """
        if self._closed:
            raise InteropError("Host target is closed")
        # An outer acquisition already made the host context current.
        if not self.acquired:
            self._make_host_current()
        return super().acquire()

    def draw(self, *, viewport=None, blend=True):
        """Draw the texture into the window's viewport (x, y, width, height), in pixels from the
        lower-left corner.

        The viewport defaults to the texture's size at (0, 0). With ``blend``, the draw composites
        Filament's premultiplied color source-over; ``blend=False`` copies the pixels.
        Outside ``acquire()``, the draw acquires and releases the texture itself.
        """
        with self.acquire():
            x, y, width, height = draw_viewport(viewport, (0, 0, self.width, self.height))
            self._draw(x, y, width, height, bool(blend))

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

    def __del__(self):
        # Garbage collection runs at arbitrary points, often while another window's context is
        # current. It must not switch contexts, so host objects are released only if this
        # target's context is already current; otherwise they go away with that context.
        if self._closed:
            return
        try:
            warnings.warn(f"{type(self).__name__} was not closed; close it or its renderer before its window",
                          ResourceWarning, source=self)
            self._close(switch=False)
        except Exception:
            # The host context or Python modules may already be gone during shutdown.
            pass


class HostTargetBase(_HostTargetLifecycle, ImportedTarget):
    """A host-owned RGBA8 texture that Filament renders into.

    Subclasses create and release the host objects and draw the texture. ``renderer.close()``
    closes the target too, so only the window must close last.
    """

    def _create_native(self, renderer, texture, width, height):
        return renderer.import_gl_target(texture, width=width, height=height,
                                         on_renderer_close=_close_with_renderer(self))


if _CopyTarget is not None:
    class CopyHostTargetBase(_HostTargetLifecycle, _CopyTarget):
        """A host-owned RGBA8 texture that receives a copy of each frame Filament renders.

        For a host context that cannot share objects with Filament's: on macOS, objects are
        shared only between contexts of one profile, and Filament needs the core profile. The
        renderer is an offscreen one. ``acquire()`` waits for the last frame and copies it into
        the texture. ``transfer`` is the path that the copy takes: ``"iosurface"`` on the GPU,
        or ``"readback"`` through CPU memory.

        Subclasses set ``_transfers``, the paths to try in order. ``fallback_reason`` is why the
        first one was unavailable, or None.
        """

        _transfers = ("iosurface", "readback")
        fallback_reason = None

        def _create_native(self, renderer, texture, width, height):
            for transfer in self._transfers:
                try:
                    return renderer._create_copy_target(texture, width=width, height=height, transfer=transfer,
                                                        on_renderer_close=_close_with_renderer(self))
                except InteropError as error:
                    if transfer == self._transfers[-1]:
                        raise
                    self.fallback_reason = f"{transfer}: {error}"
                    self._fallback(transfer, error)

        def _fallback(self, transfer, error):
            """Report that ``transfer`` was unavailable and the next one is tried."""
