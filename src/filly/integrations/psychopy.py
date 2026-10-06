"""Shared-texture integration for psychopy-lib on Windows/WGL and Linux/GLX."""

import ctypes
import sys
import weakref

from psychopy import visual
from psychopy.visual import shaders
from pyglet import gl

from .. import InteropError, current_gl_context
from .._native import _create_host_texture, _delete_host_texture
from ._host import HostTarget, shared_renderer

__all__ = ["SharedTarget", "create_renderer"]

# One ImageStim program for premultiplied color per window, which lives as long as the window's
# context. Compiling and linking it took most of the time of each as_psychopy_texture().
_programs = weakref.WeakKeyDictionary()


def _shared_program(win):
    program = _programs.get(win)
    if program is None or not gl.glIsProgram(program):
        # Filament stores premultiplied RGB. ImageStim expects straight RGB before
        # applying its color, opacity, mask, and the window's blend function.
        fragment = shaders.fragImageStim.replace(
            "vec4 maskFrag =", "textureFrag.rgb = textureFrag.a > 0.0 ? textureFrag.rgb / textureFrag.a : vec3(0.0);\nvec4 maskFrag =")
        program = shaders.compileProgram(shaders.vertSimple, fragment)
        _programs[win] = program
    return program


def _is_open(win):
    # PsychoPy keeps winHandle after close(); pyglet clears the handle's context.
    handle = getattr(win, "winHandle", None)
    return (not getattr(win, "_closed", False) and handle is not None
            and getattr(handle, "context", None) is not None)


def _current(win):
    if sys.platform not in ("win32", "linux") or win.winType != "pyglet":
        raise InteropError("The adapter requires pyglet with Windows/WGL or Linux/GLX")
    if not _is_open(win):
        raise InteropError("The PsychoPy window is closed")
    win._setCurrent()
    # PsychoPy skips the switch when it believes the window is already current, which is wrong
    # after other code switched contexts through pyglet.
    if gl.current_context is not win.winHandle.context:
        win.winHandle.switch_to()


def create_renderer(win):
    """Create a Filament engine sharing the window's native OpenGL context."""
    _current(win)
    return shared_renderer("PsychoPy")


class SharedTarget(HostTarget):
    """A PsychoPy-window RGBA8 texture that Filament renders into.

    ``colorTexture`` is the GL texture name, as PsychoPy expects of an image source.
    ``as_psychopy_texture()`` returns an ImageStim that samples it. ``draw()`` draws
    the texture with such a stimulus in pixel units.
    """

    colorTexture = gl.GLuint()
    _stimulus = _placement = None

    def __init__(self, renderer, win, width, height):
        self._win = win
        self._stimuli = weakref.WeakSet()
        super().__init__(renderer, width, height)

    def _host_open(self):
        return _is_open(self._win)

    def _make_host_current(self):
        _current(self._win)

    def _create_host_texture(self, width, height):
        self.colorTexture = gl.GLuint(_create_host_texture(width, height))
        return self.colorTexture.value

    def _draw(self, x, y, width, height):
        if self._stimulus is None:
            self._stimulus = self.as_psychopy_texture()
        columns, rows = self._win.size
        placement = (width, height), (x + (width - columns) / 2, y + (height - rows) / 2)
        # Assignment makes ImageStim rebuild its vertices, so skip it when nothing moved.
        if placement != self._placement:
            self._placement = placement
            self._stimulus.size, self._stimulus.pos = placement
        self._stimulus.draw()

    def _release_host_resources(self, host_open):
        for stimulus in list(self._stimuli):
            stimulus._detach(host_open)
        if host_open:
            _delete_host_texture(self.colorTexture.value)
        # The internal stimulus refers back to this target; drop the cycle.
        self.colorTexture, self._stimulus, self._placement = gl.GLuint(), None, None

    def as_psychopy_texture(self, win=None, *, size=None, pos=(0, 0), units="pix"):
        """Return a drawable ImageStim that samples this texture directly.

        Inside ``acquire()``, several stimuli can draw the same frame.
        """
        if self._closed:
            raise InteropError("Shared target is closed")
        if win is not None and win is not self._win:
            raise InteropError("Draw the texture in its original PsychoPy window")
        if size is None:
            if units != "pix":
                raise ValueError("Specify size when units are not 'pix'")
            size = (self.width, self.height)
        stimulus = _SharedImageStim(self, size=size, pos=pos, units=units)
        self._stimuli.add(stimulus)
        return stimulus


class _SharedImageStim(visual.ImageStim):
    @property
    def image(self):
        return self.__dict__.get("image")

    @image.setter
    def image(self, value):
        if getattr(self, "_external_texture", False) and value is not self._shared_target:
            raise InteropError("A shared stimulus cannot replace its target image")
        visual.ImageStim.image.__set__(self, value)

    def __init__(self, target, **kwargs):
        self._shared_target = target
        self._external_texture = False
        super().__init__(target._win, image=target, autoLog=False, **kwargs)
        # ImageStim allocates a placeholder even when its image supplies a GPU texture.
        gl.glDeleteTextures(1, ctypes.byref(self._texID))
        self._texID = target.colorTexture
        self._external_texture = True
        self.flipVert = False
        self._shared_program = _shared_program(target._win)

    def draw(self, win=None):
        target = self._shared_target
        if win is not None and win is not target._win:
            raise InteropError("Draw the texture in its original PsychoPy window")
        if target._win.blendMode != "avg":
            raise InteropError("Shared stimuli require PsychoPy blendMode='avg'")
        with target.acquire():
            previous = target._win._progImageStim
            try:
                target._win._progImageStim = self._shared_program
                super().draw(target._win)
            finally:
                target._win._progImageStim = previous

    def _detach(self, host_open):
        if host_open:
            self.clearTextures()
            return
        # Without the host context, a GL delete would fail or reach another
        # context. These objects go away with the context's share group.
        self._maskID = gl.GLuint()
        self._shared_program = None
        self._texID = gl.GLuint()

    def clearTextures(self):
        if not getattr(self, "_external_texture", False):
            return super().clearTextures()
        target = self._shared_target
        # ImageStim.__del__ calls this during garbage collection, when another window's context
        # can be current. Names deleted there could belong to that window's objects.
        if not (_is_open(target._win) and current_gl_context() == target._context):
            return self._detach(False)
        # The target owns the color texture and the window owns the program; ImageStim owns
        # only its mask.
        if getattr(self, "_maskID", None) and self._maskID.value:
            gl.glDeleteTextures(1, ctypes.byref(self._maskID))
            self._maskID = gl.GLuint()
        self._shared_program = None
        self._texID = gl.GLuint()
